"""Birthday announcements with a persistent per-day sent marker."""
import csv
import io
import re
import json
import logging
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

LOG = logging.getLogger('wxbot')


def read_birthdays(path):
    entries = []
    source = Path(path).read_text(encoding='utf-8-sig')
    if Path(path).suffix.lower() == '.md':
        blocks = re.findall(r'^```birthdays\s*\r?\n(.*?)^```\s*$', source, re.MULTILINE | re.DOTALL)
        if len(blocks) != 1:
            raise ValueError('SOUL.md 必须包含且只包含一个 birthdays 代码块')
        source = blocks[0]
    for number, row in enumerate(csv.reader(io.StringIO(source)), 1):
        if not row or all(not field.strip() for field in row):
            continue
        if len(row) != 3 or not row[0].strip():
            raise ValueError(f'生日名单第 {number} 行应为 姓名,月,日')
        try:
            month, day = int(row[1]), int(row[2])
            date(2000, month, day)
        except ValueError as exc:
            raise ValueError(f'生日名单第 {number} 行日期无效') from exc
        entries.append((row[0].strip(), month, day))
    return entries


def birthday_message(entries, today):
    today_names = [name for name, month, day in entries if (month, day) == (today.month, today.day)]
    today_line = (f"今天 {today.month}月{today.day}日：{'、'.join(today_names)}，生日快乐！🎂"
                  if today_names else f"今天 {today.month}月{today.day}日：没有登记的生日。")
    upcoming = []
    for offset in range(1, 8):
        day = today + timedelta(days=offset)
        names = [name for name, month, date_day in entries if (month, date_day) == (day.month, day.day)]
        if names:
            upcoming.append(f"{day.month}月{day.day}日：{'、'.join(names)}")
    week_lines = "\n".join(upcoming) if upcoming else "没有登记的生日。"
    return f"🎂 生日播报\n{today_line}\n\n接下来7天：\n{week_lines}"


def lookup_birthdays(prompt, entries, today):
    """Answer explicit name-and-date questions without relying on model arithmetic."""
    if '生日' not in prompt or not any(token in prompt for token in ('几号', '哪天', '什么时候', '几月', '多少号', '日期', '今天', '明天', '后天', '一周', '这周', '七天', '7天', '最近', '接下来')):
        return None
    matches = [(name, month, day) for name, month, day in entries if name in prompt]
    if not matches:
        if any(term in prompt for term in ('一周', '这周', '7天', '七天', '最近', '接下来')):
            return birthday_message(entries, today)
        for term, offset in (('今天', 0), ('明天', 1), ('后天', 2)):
            if term in prompt:
                day = today + timedelta(days=offset)
                names = [name for name, month, date_day in entries if (month, date_day) == (day.month, day.day)]
                return f"{term}（{day.month}月{day.day}日）：" + ('、'.join(names) + '过生日。' if names else '没有登记的生日。')
        return None
    return '；'.join(f'{name}的生日是{month}月{day}日' for name, month, day in matches) + '。'


class BirthdayScheduler:
    def __init__(self, birthdays_path, state_path, grace_minutes=10):
        self.birthdays_path = Path(birthdays_path)
        self.state_path = Path(state_path)
        self.grace = timedelta(minutes=grace_minutes)
        self.last_attempt = None
        self.announced = None
        # Fail early if the source or state cannot be read.
        read_birthdays(self.birthdays_path)
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding='utf-8-sig'))
            self.announced = date.fromisoformat(state['last_announced'])

    def tick(self, now, send):
        today = now.date()
        if self.announced == today or now - datetime.combine(today, datetime.min.time()) >= self.grace:
            return
        if self.last_attempt and now - self.last_attempt < timedelta(seconds=60):
            return
        self.last_attempt = now
        try:
            message = birthday_message(read_birthdays(self.birthdays_path), today)
        except (OSError, ValueError):
            LOG.exception('生日文件读取失败，稍后重试')
            return
        try:
            result = send(message)
        except Exception:
            # Delivery could have succeeded before the UI error; do not risk repeated sends.
            self.announced = today
            LOG.exception('生日播报发送状态未知，今天不再自动重试，请检查群聊')
            return
        if not result:
            LOG.error('生日播报未发送成功，将在一分钟后重试')
            return
        self.announced = today
        try:
            self._save(today)
        except OSError:
            LOG.exception('生日播报已发送，但记录日期失败；重启后可能重复播报')
        LOG.info('生日播报已发送')

    def _save(self, today):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.birthday-', suffix='.tmp', dir=self.state_path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as file:
                json.dump({'last_announced': today.isoformat()}, file)
            os.replace(temporary, self.state_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

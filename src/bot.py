import argparse
import contextlib
import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime

from .birthdays import BirthdayScheduler, birthday_message, lookup_birthdays, read_birthdays
from pathlib import Path

LOG = logging.getLogger("wxbot")


def load_config(path):
    with Path(path).open(encoding="utf-8-sig") as f:
        config = json.load(f)
    config.setdefault("ollama_url", "http://127.0.0.1:11434")
    config.setdefault("model", "qwen2.5:3b")
    soul_path = Path(config.get("soul_file", "SOUL.md"))
    if not soul_path.is_absolute():
        soul_path = Path(path).resolve().parent / soul_path
    config["soul_path"] = str(soul_path)
    config["system_prompt"] = soul_path.read_text(encoding="utf-8-sig").strip()
    if not config["system_prompt"]:
        raise ValueError(f"人设文件为空: {soul_path}")
    config.setdefault("history_turns", 6)
    config.setdefault("timeout_seconds", 180)
    config.setdefault("discover_chats", False)
    if not isinstance(config["discover_chats"], bool):
        raise ValueError("discover_chats 必须为 true 或 false")
    birthday_config = config.get("birthdays")
    if birthday_config:
        if not isinstance(birthday_config, dict) or not all(birthday_config.get(k) for k in ("file", "state_file", "chat")):
            raise ValueError("birthdays 必须包含 file、state_file 和 chat")
        for key in ("file", "state_file"):
            path_value = Path(birthday_config[key])
            birthday_config[key] = str(path_value if path_value.is_absolute() else Path(path).resolve().parent / path_value)
    chats = config.get("chats", [])
    if not isinstance(chats, list):
        raise ValueError("chats 必须是数组")
    names = set()
    for chat in chats:
        if not isinstance(chat, dict) or not isinstance(chat.get("name"), str) or not chat["name"].strip():
            raise ValueError("每个聊天必须填写 name")
        if chat["name"] in names:
            raise ValueError("聊天名称不能重复")
        names.add(chat["name"])
        if chat.get("type", "private") not in ("private", "group"):
            raise ValueError("聊天 type 必须为 private 或 group")
        if "bot_name" in chat and (not isinstance(chat["bot_name"], str) or not chat["bot_name"].strip()):
            raise ValueError("bot_name 必须是非空字符串")
    if not isinstance(config["history_turns"], int) or isinstance(config["history_turns"], bool) or config["history_turns"] < 0:
        raise ValueError("history_turns 必须是非负整数")
    if config["timeout_seconds"] <= 0:
        raise ValueError("超时必须大于 0")
    return config


class OllamaChat:
    def __init__(self, config):
        self.config = config
        self.history = defaultdict(list)

    def reply(self, key, prompt):
        turns = self.config["history_turns"]
        history = self.history[key][-turns * 2:] if turns else []
        system_prompt = Path(self.config["soul_path"]).read_text(encoding="utf-8-sig").strip()
        if not system_prompt:
            raise ValueError("SOUL.md 为空")
        today = datetime.now().date()
        birthday_config = self.config.get("birthdays")
        if birthday_config:
            entries = read_birthdays(birthday_config["file"])
            direct_answer = lookup_birthdays(prompt, entries, today)
            if direct_answer:
                return direct_answer
            system_prompt += f"\n\n当前本地日期：{today.isoformat()}。\n" + birthday_message(entries, today)
        messages = [{"role": "system", "content": system_prompt}, *history,
                    {"role": "user", "content": prompt}]
        payload = json.dumps({"model": self.config["model"], "messages": messages, "stream": False}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.config["ollama_url"].rstrip("/") + "/api/chat", data=payload,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.config["timeout_seconds"]) as response:
            result = json.load(response)
        answer = result.get("message", {}).get("content", "").strip()
        if not answer:
            raise ValueError("Ollama 返回了空回复")
        return answer

    def remember(self, key, prompt, answer):
        turns = self.config["history_turns"]
        if turns:
            self.history[key] = (self.history[key] + [{"role": "user", "content": prompt},
                                                      {"role": "assistant", "content": answer}])[-turns * 2:]


def message_prompt(message, chat, account_name=None):
    content = getattr(message, "content", "")
    if not isinstance(content, str):
        return None
    content = content.strip()
    if not content:
        return None
    attr = getattr(message, "attr", None)
    kind = getattr(message, "type", None)
    if chat.get("type", "private") != "group":
        return content if attr == "friend" and kind == "text" else None

    name = chat.get("bot_name") or account_name
    if not name:
        raise ValueError("无法获取机器人昵称，请在群聊配置中设置 bot_name")
    if attr == "friend" and kind == "text":
        # 微信使用 U+2005 分隔 @昵称和正文；不要匹配其他成员或 @所有人。
        mention = re.compile(r"@" + re.escape(name) + r"(?=$|[\s，。！？、,:：;；!?])")
        if not mention.search(content):
            return None
        prompt = mention.sub("", content, count=1).strip()
        return prompt or "有人在群里@了你，请简短回应。"
    if attr == "system" and kind in ("system", "tickle"):
        # 微信 4.1.8 的拍一拍消息格式："发送者" 拍了拍 "接收者"。
        match = re.match(r'^\s*["“]([^"”]+)["”]\s*拍了拍\s*["“]([^"”]+)["”]', content)
        if match and match.group(1) not in ("我", "你", name) and match.group(2) in ("我", "你", name):
            return "有人在群里拍了拍你，请简短回应。"
    return None


def new_messages(previous_ids, messages):
    """Find messages after the latest visible item from the previous snapshot."""
    current_ids = [item.id for item in messages]
    if not previous_ids:
        return messages, current_ids
    positions = {ident: index for index, ident in enumerate(current_ids)}
    for ident in reversed(previous_ids):
        if ident in positions:
            return messages[positions[ident] + 1:], current_ids
    # A refreshed or scrolled window has no reliable overlap: rebaseline.
    LOG.warning("聊天窗口消息列表已变化，已重新建立基线；这段时间的消息可能遗漏")
    return [], current_ids


def run(config):
    if not config["chats"]:
        raise ValueError("请先在 config.json 的 chats 中添加要监听的聊天")
    try:
        from wxauto4 import WeChat
    except ImportError as exc:
        raise RuntimeError("无法导入 wxauto4，请先安装 requirements.txt") from exc
    wx = WeChat(ads=False, start_listener=False)
    llm = OllamaChat(config)
    birthday_config = config.get("birthdays")
    scheduler = BirthdayScheduler(birthday_config["file"], birthday_config["state_file"]) if birthday_config else None
    windows = []
    for chat in config["chats"]:
        name = chat["name"]
        window = wx.GetSubWindow(name)
        if not window:
            wx.ChatWith(name, exact=True)
            sessions = [item for item in wx.SessionBox.get_session() if item.name == name]
            if len(sessions) != 1:
                raise RuntimeError(f"无法唯一定位聊天 {name}；请手动双击打开独立窗口后重试")
            sessions[0].double_click()
            time.sleep(1)
            window = wx.GetSubWindow(name)
        if not window:
            raise RuntimeError(f"无法打开 {name} 的独立聊天窗口")
        baseline = [item.id for item in window.GetAllMessage()]
        windows.append([chat, window, baseline, False])
        LOG.info("正在监听: %s", name)

    if scheduler and not any(state[0]["name"] == birthday_config["chat"] and state[0].get("type") == "group" for state in windows):
        raise ValueError("生日播报群必须在 chats 中配置为 group")

    # Remember unread counts on startup so old unread chats are not answered.
    unread_seen = {}
    def scan_sessions(initial=False):
        for session in wx.GetSession():
            name = session.name
            if not name or not session.isnew:
                unread_seen.pop(name, None)
                continue
            count = max(int(session.new_count or 0), 1)
            old_count = unread_seen.get(name, 0)
            unread_seen[name] = count
            if initial or any(state[0]["name"] == name for state in windows) or count <= old_count:
                continue
            try:
                wx.ChatWith(name, exact=True)
                info = wx.ChatInfo()
                kind = info.get("chat_type")
                if info.get("chat_name") != name or kind not in ("friend", "group"):
                    LOG.warning("跳过无法确认类型或名称的会话: %s", name)
                    continue
                session_matches = [item for item in wx.SessionBox.get_session() if item.name == name]
                if len(session_matches) != 1:
                    LOG.warning("无法唯一定位会话，跳过: %s", name)
                    continue
                window = wx.GetSubWindow(name)
                if not window:
                    session_matches[0].double_click()
                    time.sleep(1)
                    window = wx.GetSubWindow(name)
                if not window:
                    LOG.warning("无法打开会话独立窗口: %s", name)
                    continue
                messages = window.GetAllMessage()
                # Process only newly observed unread messages, not older visible history.
                fresh_count = min(count - old_count, len(messages))
                prior = [message.id for message in messages[:-fresh_count]] if fresh_count else [message.id for message in messages]
                settings = {"name": name, "type": "group" if kind == "group" else "private"}
                windows.append([settings, window, prior, False])
                LOG.info("发现新会话: %s", name)
            except Exception:
                LOG.exception("发现会话失败: %s", name)

    if config["discover_chats"]:
        try:
            scan_sessions(initial=True)
        except Exception:
            LOG.exception("初始化会话发现失败")
    next_scan = 0.0
    while True:
        if config["discover_chats"] and time.monotonic() >= next_scan:
            try:
                scan_sessions()
            except Exception:
                LOG.exception("扫描会话失败")
            next_scan = time.monotonic() + 5
        if scheduler:
            target = next(state for state in windows if state[0]["name"] == birthday_config["chat"])
            if not target[3]:
                scheduler.tick(datetime.now(), target[1].SendMsg)
        for state in windows:
            chat, window, baseline, needs_rebaseline = state
            try:
                if needs_rebaseline:
                    window = wx.GetSubWindow(chat["name"])
                    if not window:
                        continue
                    state[1] = window
                messages = window.GetAllMessage()
                if needs_rebaseline:
                    state[2] = [item.id for item in messages]
                    state[3] = False
                    LOG.info("聊天窗口已恢复，已重新建立消息基线: %s", chat["name"])
                    continue
                fresh, state[2] = new_messages(baseline, messages)
            except Exception:
                if not needs_rebaseline:
                    LOG.exception("读取聊天窗口失败，等待恢复: %s", chat["name"])
                state[3] = True
                continue
            for message in fresh:
                prompt = message_prompt(message, chat, wx.nickname)
                if prompt is None:
                    continue
                name = chat["name"]
                try:
                    if prompt == "/reset":
                        llm.history.pop(name, None)
                        answer = "对话上下文已清空。"
                    else:
                        answer = llm.reply(name, prompt)
                    result = window.SendMsg(answer)
                    if not result:
                        raise RuntimeError(f"微信发送失败: {result}")
                    if prompt != "/reset":
                        llm.remember(name, prompt, answer)
                    LOG.info("已回复聊天: %s", name)
                except Exception:
                    LOG.exception("处理聊天 %s 失败，跳过该消息", name)
        time.sleep(1)


@contextlib.contextmanager
def single_instance():
    # Prevent two copies from both sending the same scheduled announcement.
    import msvcrt
    lock_path = Path(__file__).resolve().parents[1] / "data" / "wxbot.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("机器人已经运行，请先关闭已有实例") from exc
        try:
            yield
        finally:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def main():
    parser = argparse.ArgumentParser(description="微信桌面版 + 本地 Ollama 自动回复")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "config" / "config.json"))
    parser.add_argument("--test-ollama", action="store_true", help="只测试 Ollama，不连接微信")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = load_config(args.config)
        if args.test_ollama:
            print(OllamaChat(config).reply("test", "请用一句话介绍你自己。"))
        else:
            with single_instance():
                run(config)
    except KeyboardInterrupt:
        print("\n已停止。")
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        parser.exit(1, f"启动失败: {exc}\n")


if __name__ == "__main__":
    main()

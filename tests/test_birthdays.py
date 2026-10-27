import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from src.birthdays import BirthdayScheduler, birthday_message, read_birthdays, lookup_birthdays


class BirthdayTests(unittest.TestCase):
    def test_read_and_match_multiple(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'birthdays.txt'
            path.write_text('甲,1,2\n乙,1,2\n丙,2,29\n', encoding='utf-8')
            data = read_birthdays(path)
            self.assertIn('甲、乙', birthday_message(data, date(2026, 1, 2)))
            self.assertIn('没有登记', birthday_message(data, date(2026, 1, 3)))
            path.write_text('错,2,30\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                read_birthdays(path)

    def test_soul_block_and_upcoming_cross_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'SOUL.md'
            path.write_text('# 人设\n```birthdays\n甲,12,30\n乙,1,1\n丙,1,6\n丁,1,7\n```\n', encoding='utf-8')
            entries = read_birthdays(path)
            result = birthday_message(entries, date(2026, 12, 30))
            self.assertIn('今天 12月30日：甲', result)
            self.assertIn('1月1日：乙', result)
            self.assertIn('1月6日：丙', result)
            self.assertNotIn('丁', result)
            self.assertEqual(lookup_birthdays('乙的生日是哪天？', entries, date(2026, 12, 30)), '乙的生日是1月1日。')
            self.assertIn('乙', lookup_birthdays('这周谁生日？', entries, date(2026, 12, 30)))
            self.assertIn('没有登记', lookup_birthdays('明天谁生日？', entries, date(2026, 12, 30)))
            path.write_text('# 没有名单', encoding='utf-8')
            with self.assertRaises(ValueError):
                read_birthdays(path)

    def test_midnight_once_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, state = Path(tmp) / 'birthdays.txt', Path(tmp) / 'state.json'
            path.write_text('甲,1,2\n', encoding='utf-8')
            sent = []
            send = lambda message: sent.append(message) or True
            scheduler = BirthdayScheduler(path, state)
            scheduler.tick(datetime(2026, 1, 1, 23, 59, 59), send)
            self.assertEqual(sent, [])
            scheduler.tick(datetime(2026, 1, 2, 0, 0), send)
            scheduler.tick(datetime(2026, 1, 2, 0, 1), send)
            BirthdayScheduler(path, state).tick(datetime(2026, 1, 2, 0, 2), send)
            self.assertEqual(len(sent), 1)
            self.assertIn('甲', sent[0])
            BirthdayScheduler(path, state).tick(datetime(2026, 1, 3, 0, 11), send)
            self.assertEqual(len(sent), 1)

    def test_failed_send_retries_after_one_minute(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, state = Path(tmp) / 'birthdays.txt', Path(tmp) / 'state.json'
            path.write_text('甲,1,2\n', encoding='utf-8')
            scheduler = BirthdayScheduler(path, state)
            attempts = []
            def send(message):
                attempts.append(message)
                return len(attempts) > 1
            scheduler.tick(datetime(2026, 1, 2, 0, 0), send)
            scheduler.tick(datetime(2026, 1, 2, 0, 0, 30), send)
            scheduler.tick(datetime(2026, 1, 2, 0, 1), send)
            self.assertEqual(len(attempts), 2)
            self.assertTrue(state.exists())

if __name__ == '__main__':
    unittest.main()

import unittest
import tempfile
import shutil
from pathlib import Path
from types import SimpleNamespace
from src.bot import message_prompt, OllamaChat, new_messages, load_config

class BotTests(unittest.TestCase):
    def test_group_triggers_only_target(self):
        msg = lambda attr, kind, content: SimpleNamespace(attr=attr, type=kind, content=content)
        group = {"type": "group"}
        self.assertEqual(message_prompt(msg("friend", "text", "@hyp\u2005你好"), group, "hyp"), "你好")
        self.assertIsNone(message_prompt(msg("friend", "text", "@hyper 你好"), group, "hyp"))
        self.assertIsNone(message_prompt(msg("friend", "text", "你好"), group, "hyp"))
        self.assertIsNone(message_prompt(msg("self", "text", "@hyp 你好"), group, "hyp"))
        self.assertIsNotNone(message_prompt(msg("system", "system", '"小明" 拍了拍 "hyp"'), group, "hyp"))
        self.assertIsNotNone(message_prompt(msg("system", "system", '"小明" 拍了拍 "我"'), group, "hyp"))
        self.assertIsNone(message_prompt(msg("system", "system", '"小明" 拍了拍 "别人"'), group, "hyp"))
        self.assertIsNone(message_prompt(msg("system", "system", '"我" 拍了拍 "hyp"'), group, "hyp"))
        self.assertEqual(message_prompt(msg("friend", "text", "@群名\u2005你好"), {"type":"group", "bot_name":"群名"}, "hyp"), "你好")

    def test_snapshot_overlap_and_rebaseline(self):
        messages = [SimpleNamespace(id=str(i)) for i in (2, 3, 4)]
        fresh, ids = new_messages(["1", "2", "3"], messages)
        self.assertEqual([item.id for item in fresh], ["4"])
        self.assertEqual(ids, ["2", "3", "4"])
        fresh, _ = new_messages(["old"], messages)
        self.assertEqual(fresh, [])

    def test_history_isolated_and_bounded(self):
        bot = OllamaChat({"history_turns": 1})
        bot.remember("a", "one", "first")
        bot.remember("a", "two", "second")
        bot.remember("b", "other", "answer")
        self.assertEqual([m["content"] for m in bot.history["a"]], ["two", "second"])
        self.assertEqual(bot.history["b"][0]["content"], "other")

    def test_config_reads_soul(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy("config/config.example.json", root / "config.json")
            shutil.copy("config/SOUL.example.md", root / "SOUL.md")
            config = load_config(root / "config.json")
            self.assertIn("嘴硬", config["system_prompt"])
            self.assertEqual(config["chats"][0]["name"], "YY")

if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.bot import run


class DiscoveryTests(unittest.TestCase):
    def test_discovers_and_replies_to_new_private_chat(self):
        message = SimpleNamespace(id='new', attr='friend', type='text', content='你好')
        sent = []
        class Window:
            def __init__(self, messages): self.messages = messages
            def GetAllMessage(self): return self.messages
            def SendMsg(self, text): sent.append(text); return True
        class FakeWeChat:
            nickname = 'hyp'
            def __init__(self, **kwargs):
                self.calls = 0
                self.SessionBox = SimpleNamespace(get_session=lambda: [session])
                self.windows = {'YY': Window([]), '测试好友': Window([message])}
            def GetSubWindow(self, name): return self.windows.get(name)
            def GetSession(self):
                self.calls += 1
                return [] if self.calls == 1 else [session]
            def ChatWith(self, name, exact=True): pass
            def ChatInfo(self): return {'chat_type': 'friend', 'chat_name': '测试好友'}
        session = SimpleNamespace(name='测试好友', isnew=True, new_count=1)
        class FakeLLM:
            def __init__(self, config): pass
            def reply(self, key, prompt): return '测试回复'
            def remember(self, key, prompt, answer): pass
        config = {'chats': [{'name': 'YY', 'type': 'group'}], 'discover_chats': True}
        with patch.dict('sys.modules', {'wxauto4': SimpleNamespace(WeChat=FakeWeChat)}), \
             patch('src.bot.OllamaChat', FakeLLM), \
             patch('src.bot.time.sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                run(config)
        self.assertEqual(sent, ['测试回复'])

if __name__ == '__main__':
    unittest.main()

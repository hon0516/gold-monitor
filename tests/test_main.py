import asyncio
import unittest

from fastapi import HTTPException

from app.main import ConnectionManager, parse_recipients_api


class FakeWebSocket:
    def __init__(self, fail=False):
        self.accepted = False
        self.fail = fail
        self.messages = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        if self.fail:
            raise RuntimeError("closed")
        self.messages.append(payload)


class ConnectionManagerTests(unittest.TestCase):
    def test_connect_broadcast_and_drop_failed_connection(self):
        async def scenario():
            manager = ConnectionManager()
            good = FakeWebSocket()
            bad = FakeWebSocket(fail=True)

            await manager.connect(good)
            await manager.connect(bad)
            await manager.broadcast({"type": "snapshot"})

            self.assertTrue(good.accepted)
            self.assertEqual(good.messages, [{"type": "snapshot"}])
            self.assertIn(good, manager.active_connections)
            self.assertNotIn(bad, manager.active_connections)

        asyncio.run(scenario())


class ApiHelperTests(unittest.TestCase):
    def test_parse_recipients_api_rejects_invalid_email_as_client_error(self):
        async def scenario():
            with self.assertRaises(HTTPException) as ctx:
                await parse_recipients_api({"value": "ok@example.com, bad-email"})
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("Invalid recipient emails", ctx.exception.detail)

        asyncio.run(scenario())

    def test_parse_recipients_api_accepts_user_email(self):
        async def scenario():
            result = await parse_recipients_api({"value": "liangjiahong0516@gmail.com"})
            self.assertEqual(result, {"recipients": ["liangjiahong0516@gmail.com"]})

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

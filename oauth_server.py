from aiohttp import web
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def handle_oauth_callback(request):
    code = request.query.get('code')
    if code:
        logger.info(f"Received OAuth code: {code}")
        return web.Response(text=f"Код авторизации получен: {code}\nПожалуйста, скопируйте его и отправьте боту.")
    return web.Response(text="Ошибка: код не получен")


async def start_server():
    app = web.Application()
    app.router.add_get('/oauth2callback', handle_oauth_callback)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8080)
    await site.start()

    logger.info("OAuth callback server started at http://localhost:8080")

    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(start_server())
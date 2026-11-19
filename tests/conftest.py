import asyncio
import sys

if sys.platform == "win32":
    # psycopg async nao suporta o ProactorEventLoop (padrao no Windows).
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

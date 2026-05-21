import asyncio

from bleak import BleakScanner

async def main():

    devices = await BleakScanner.discover(timeout=5)

    for d in devices:

        print("Name:", d.name, " Address:", d.address)

asyncio.run(main())
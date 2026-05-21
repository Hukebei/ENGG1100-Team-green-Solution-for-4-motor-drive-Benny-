import asyncio

from bleak import BleakClient

DEVICE_ADDRESS = "FB39458D-5F01-B0CE-AFA4-BC6A781B3407"

async def main():

    async with BleakClient(DEVICE_ADDRESS) as client:

        print("Connected:", client.is_connected)

        for service in client.services:

            print("\nService:", service.uuid)

            for char in service.characteristics:

                print("  Characteristic:", char.uuid)

                print("  Properties:", char.properties)

asyncio.run(main())
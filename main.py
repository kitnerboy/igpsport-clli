import asyncio
import os
import struct
from bleak import BleakScanner, BleakClient

DEVICE_NAME_PREFIX = "BSC300"

# Точные UUID из вашего HCI Snoop лога
SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
WRITE_UUID   = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NOTIFY_UUID  = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

def prepare_start_header(filename: str) -> bytes:
    """
    Формирует заголовок инициализации в точном соответствии с дампом Wireshark.
    Префикс: 0x32 0x09 + 'track.fit: fit    |  .FIT'
    """
    prefix = bytes([0x32, 0x09])

    # Форматируем имя под формат iGPSPORT из лога
    header_str = f"{filename}: fit    |  .FIT"

    return prefix + header_str.encode('utf-8')

async def notification_handler(sender, data: bytearray):
    print(f"\n[<<< ACK от BSC300]: {data.hex(' ')}")

async def send_track_file(file_path: str):
    if not os.path.exists(file_path):
        print(f"Ошибка: Файл {file_path} не найден.")
        return

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    file_name = os.path.basename(file_path)

    print(f"Поиск {DEVICE_NAME_PREFIX}...")
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: d.name and DEVICE_NAME_PREFIX in d.name,
        timeout=10.0
    )
    if not device:
        print("BSC300 не найден!")
        return

    print(f"Подключение к {device.address}...")

    async with BleakClient(device) as client:
        print("Соединение установлено!")

        # 1. Подписка на ответы (NOTIFY)
        await client.start_notify(NOTIFY_UUID, notification_handler)
        await asyncio.sleep(0.5)

        # 2. Отправка стартовой команды (0x32 0x09 + Header)
        header_packet = prepare_start_header(file_name)
        print(f"\n[>>> ИНИЦИАЛИЗАЦИЯ]: Отправка заголовка...")
        print(f"      HEX: {header_packet.hex(' ')}")

        # ВАЖНО: Передача без ответа (write-without-response / response=False)
        await client.write_gatt_char(WRITE_UUID, header_packet, response=False)
        await asyncio.sleep(0.5)

        # 3. Передача данных файла чанками по 244 байта (размер из лога)
        chunk_size = 244
        total_chunks = (len(file_bytes) + chunk_size - 1) // chunk_size
        print(f"\n[>>> ПЕРЕДАЧА]: Отправка {file_name} ({len(file_bytes)} байт)...")

        for i in range(0, len(file_bytes), chunk_size):
            chunk = file_bytes[i:i + chunk_size]
            await client.write_gatt_char(WRITE_UUID, chunk, response=False)
            await asyncio.sleep(0.02)  # Задержка 20мс между чанками

            progress = ((i // chunk_size) + 1) / total_chunks * 100
            print(f"\rПрогресс: {progress:.1f}%", end="", flush=True)

        print("\n\n[>>> ДАННЫЕ УСПЕШНО ОТПРАВЛЕНЫ]")
        await asyncio.sleep(2.0)
        await client.stop_notify(NOTIFY_UUID)
        print("Сессия завершена.")

if __name__ == "__main__":
    # Укажите путь к вашему .fit файлу
    TRACK_FILE = os.path.expanduser("/tmp/track.fit")
    asyncio.run(send_track_file(TRACK_FILE))
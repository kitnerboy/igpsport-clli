import asyncio
import os
import struct
from bleak import BleakScanner, BleakClient

DEVICE_NAME_PREFIX = "BSC300"  # Имя вашего устройства

def prepare_file_header(filename: str, file_data: bytes) -> bytes:
    opcode = 0x01        # Start Upload
    file_type = 0x02     # 0x01 или 0x02 = Route / Track File
    file_size = len(file_data)
    name_bytes = filename.encode('utf-8')
    name_len = len(name_bytes)

    # Структура: OpCode (1B) + FileType (1B) + FileSize (4B) + NameLen (1B) + Name
    header = struct.pack("<BB I B", opcode, file_type, file_size, name_len) + name_bytes
    return header


async def notification_handler(sender, data):
    """Обработчик ответов (ACK) от велокомпьютера"""
    print(f"\n[ACK от BSC300]: {data.hex()}")

def resolve_characteristics(client):
    """
    Динамически ищет сервисы и характеристики для записи и уведомлений,
    игнорируя стандартные службы BLE (000018xx).
    """
    write_char = None
    notify_char = None

    for service in client.services:
        # Пропускаем стандартные BLE сервисы (Generic Access, Battery Service и т.д.)
        if service.uuid.lower().startswith("000018"):
            continue

        for char in service.characteristics:
            props = char.properties

            # Ищем характеристику с поддержкой уведомлений
            if ("notify" in props or "indicate" in props) and not notify_char:
                notify_char = char.uuid

            # Ищем характеристику с поддержкой записи
            if ("write" in props or "write-without-response" in props) and not write_char:
                write_char = char.uuid

    return write_char, notify_char

async def send_track_file(file_path: str, mac_address: str = None):
    if not os.path.exists(file_path):
        print(f"Ошибка: Файл {file_path} не найден.")
        return

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    file_name = os.path.basename(file_path)

    # 1. Поиск устройства
    print(f"Поиск {DEVICE_NAME_PREFIX} по Bluetooth...")
    if not mac_address:
        device = await BleakScanner.find_device_by_filter(
            lambda d, ad: d.name and DEVICE_NAME_PREFIX in d.name,
            timeout=10.0
        )
        if not device:
            print("Устройство не найдено в эфире. Убедитесь, что BSC300 включен.")
            return
        mac_address = device.address

    print(f"Подключение к {mac_address}...")

    async with BleakClient(mac_address) as client:
        if not client.is_connected:
            print("Не удалось подключиться.")
            return

        print("Успешно подключено!")

        # 2. Динамический поиск характеристик WRITE и NOTIFY
        write_char_uuid, notify_char_uuid = resolve_characteristics(client)

        if not write_char_uuid or not notify_char_uuid:
            print("\nОшибка: Не удалось автоматически определить характеристики для обмена данными.")
            print("Доступные сервисы и характеристики на устройстве:")
            for s in client.services:
                print(f" Service: {s.uuid}")
                for c in s.characteristics:
                    print(f"   └── Char: {c.uuid} | Props: {c.properties}")
            return

        print(f"Используем WRITE  UUID: {write_char_uuid}")
        print(f"Используем NOTIFY UUID: {notify_char_uuid}")

        # 3. Подписка на уведомления
        await client.start_notify(notify_char_uuid, notification_handler)

        # 4. Отправка заголовка с метаданными файла
        header_packet = prepare_file_header(file_name, file_bytes)
        print(f"Инициализация передачи файла {file_name} ({len(file_bytes)} байт)...")
        await client.write_gatt_char(write_char_uuid, header_packet, response=True)
        await asyncio.sleep(1.0)

        # 5. Передача бинарных данных файла
        chunk_size = 100  # Безопасный размер пакета для BLE
        total_chunks = (len(file_bytes) + chunk_size - 1) // chunk_size

        print("Передача данных трека...")
        for i in range(0, len(file_bytes), chunk_size):
            chunk = file_bytes[i:i + chunk_size]
            await client.write_gatt_char(write_char_uuid, chunk, response=False)

            await asyncio.sleep(0.02)  # Небольшая пауза между пакетами

            progress = ((i // chunk_size) + 1) / total_chunks * 100
            print(f"\rПрогресс: {progress:.1f}%", end="")

        # По окончании отправки всех чанков:
        print("\nПередача завершающего пакета...")
        end_packet = struct.pack("<BB", 0x02, 0x00)  # OpCode 0x02 = Finish Transfer
        await client.write_gatt_char(write_char_uuid, end_packet, response=True)

        print("\nФайл передан! Ожидание сохранения устройством...")
        await asyncio.sleep(2.0)
        await client.stop_notify(notify_char_uuid)

if __name__ == "__main__":
    TRACK_FILE = os.path.expanduser("/tmp/test-send-trackk.fit")
    asyncio.run(send_track_file(TRACK_FILE))

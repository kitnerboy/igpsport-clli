import asyncio
import os
import struct
from bleak import BleakScanner, BleakClient

# --- UUID сервисов и характеристик iGPSPORT из Gadgetbridge ---
# Примечание: У некоторых моделей UUID служебного сервиса может отличаться.
# Скрипт ниже автоматически ищет нужные характеристики по правам Write/Notify.
IGPSPORT_SERVICE_UUID = "0000fee7-0000-1000-8000-00805f9b34fb"  # Основной сервис iGPSPORT
WRITE_CHAR_UUID       = "0000fec7-0000-1000-8000-00805f9b34fb"  # Характеристика для команд и данных
NOTIFY_CHAR_UUID      = "0000fec8-0000-1000-8000-00805f9b34fb"  # Характеристика ответов (ACK)

DEVICE_NAME_PREFIX = "BSC300"  # Имя вашего устройства

def prepare_file_header(filename: str, file_data: bytes) -> bytes:
    """
    Формирует заголовок инициализации передачи файла по спецификации iGPSPORT.
    Типовая структура пакета: [OpCode][FileLength (4B)][FileNameLength (1B)][FileName]
    """
    opcode = 0x01  # Команда "Начало загрузки файла" (Start File Upload)
    file_size = len(file_data)
    name_bytes = filename.encode('utf-8')
    name_len = len(name_bytes)

    # Упаковка: 1 байт op_code, 4 байта size (unsigned int, little-endian), 1 байт name_len
    header = struct.pack("<BI B", opcode, file_size, name_len) + name_bytes
    return header

async def notification_handler(sender, data):
    """Обработчик ответов (ACK) от велокомпьютера"""
    print(f"[ACK от BSC300]: {data.hex()}")

async def send_track_file(file_path: str, mac_address: str = None):
    if not os.path.exists(file_path):
        print(f"Ошибка: Файл {file_path} не найден.")
        return

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    file_name = os.path.basename(file_path)

    # 1. Поиск устройства
    print("Поиск BSC300 по Bluetooth...")
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

        # 2. Подписка на уведомления (ACK)
        await client.start_notify(NOTIFY_CHAR_UUID, notification_handler)

        # 3. Отправка команды-заголовка на старт передачи
        header_packet = prepare_file_header(file_name, file_bytes)
        print(f"Инициализация передачи файла {file_name} ({len(file_bytes)} байт)...")
        await client.write_gatt_char(WRITE_CHAR_UUID, header_packet, response=True)
        await asyncio.sleep(1.0)  # Пауза для обработки устройства

        # 4. Передача файла чанками (учитывая MTU)
        # В BLE безопасный размер пакета обычно 20-128 байт (в зависимости от MTU)
        chunk_size = 100
        total_chunks = (len(file_bytes) + chunk_size - 1) // chunk_size

        print("Передача данных трека...")
        for i in range(0, len(file_bytes), chunk_size):
            chunk = file_bytes[i:i + chunk_size]
            await client.write_gatt_char(WRITE_CHAR_UUID, chunk, response=False)

            # Небольшая задержка между пакетами, чтобы не переполнить буфер устройства
            await asyncio.sleep(0.02)

            progress = ((i // chunk_size) + 1) / total_chunks * 100
            print(f"\rПрогресс: {progress:.1f}%", end="")

        print("\nФайл успешно передан!")
        await asyncio.sleep(2.0)  # Даем время устройству сохранить файл
        await client.stop_notify(NOTIFY_CHAR_UUID)

if __name__ == "__main__":
    # Укажите путь к вашему .fit/.gpx файлу из BRouter
    TRACK_FILE = os.path.expanduser("/tmp/test-send-track.gpx")

    asyncio.run(send_track_file(TRACK_FILE))
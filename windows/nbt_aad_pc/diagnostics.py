"""Head-unit restart using a single UDS ECUReset over the diagnostic gateway."""
import struct
import time
from dataclasses import replace

from .discovery import find_gateways
from .i18n import tr
from .relay import (RelayError, RelaySettings, _read_exact, _verify_head_unit_response,
                    connect_car, discover_gateway)


def restart(settings, log=lambda _: None, *, connector=connect_car, discover=find_gateways,
            gateway_finder=discover_gateway, timeout=10):
    settings.validate()
    if not settings.gateway:
        matches = list(dict.fromkeys((host, str(adapter.ipv4.ip))
                                     for host, adapter in discover(settings.source_ip, log)))
        if len(matches) > 1:
            raise RelayError(tr('Найдено несколько шлюзов BMW. Укажите IP диагностики вручную.'))
        if matches:
            gateway, source = matches[0]
            settings = replace(settings, gateway=gateway, source_ip=source)
        else:
            settings = replace(settings, gateway=gateway_finder(settings.source_ip, log=log))
    _verify_head_unit_response(settings.gateway, settings.source_ip, connector)
    log(tr('Отправляю ECUReset 11 01 для ГУ 0x63 через %s:6801.') % settings.gateway)
    with connector(settings.gateway, 6801, settings.source_ip, timeout=3) as car:
        car.sendall(bytes.fromhex('00 00 00 04 00 01 f4 63 11 01'))
        deadline = time.monotonic() + timeout
        try:
            while True:
                length, control = struct.unpack('>IH', _read_exact(car, 6, deadline))
                if length > 65536:
                    raise RelayError(tr('Недопустимая длина ответа HSFZ.'))
                data = _read_exact(car, length, deadline)
                if control in (0x43, 0x45):
                    raise RelayError(tr('Шлюз сообщил о недоступности ГУ для перезагрузки.'))
                if control != 1 or data[:2] != b'\x63\xf4':
                    continue
                if data == b'\x63\xf4\x51\x01':
                    return tr('ГУ подтвердило ECUReset ответом 51 01. Дождитесь загрузки штатного меню.')
                if len(data) == 5 and data[2:4] == b'\x7f\x11':
                    if data[4] == 0x78:
                        continue
                    raise RelayError(tr('ГУ отклонило ECUReset (NRC 0x%02X).') % data[4])
        except OSError as exc:
            raise RelayError(tr('Запрос ECUReset отправлен, ответ ГУ не получен. Проверьте перезагрузку ГУ.')) from exc

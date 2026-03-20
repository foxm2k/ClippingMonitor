import logging

from pymodbus.client import AsyncModbusTcpClient

logger = logging.getLogger(__name__)


class FroniusModbusClient:
    """Modbus-TCP-Client fuer den Fronius Wechselrichter (SunSpec)."""

    def __init__(
        self,
        host: str = "192.168.123.79",
        port: int = 502,
        slave_id: int = 1,
    ):
        self.host = host
        self.port = port
        self.slave_id = slave_id

    async def test_connection(self) -> dict:
        """Verbindet sich mit dem Wechselrichter, liest Register 40000-40001
        und prueft ob der SunSpec-Marker 'SunS' vorhanden ist.

        SunSpec codiert 'SunS' als zwei 16-Bit-Register:
          40000 = 0x5375 = 21365  ('Su')
          40001 = 0x6E53 = 28243  ('nS')
        """
        client = AsyncModbusTcpClient(self.host, port=self.port)

        try:
            connected = await client.connect()
            if not connected:
                logger.error("Modbus-Verbindung zu %s:%d fehlgeschlagen", self.host, self.port)
                return {"success": False, "error": "Verbindung fehlgeschlagen"}

            logger.info("Modbus-Verbindung zu %s:%d hergestellt", self.host, self.port)

            # Fronius-Adressierung: Fronius nennt den SunSpec-Marker "Register 40001"
            # pymodbus-Adresse = Fronius-Register - 1 = 40000
            result = await client.read_holding_registers(
                address=40000,
                count=2,
                device_id=self.slave_id,
            )

            if result.isError():
                logger.error("Fehler beim Lesen der Register: %s", result)
                return {"success": False, "error": str(result)}

            reg_values = result.registers
            logger.info("Register 40000-40001: %s", reg_values)

            # SunSpec-Marker pruefen
            is_sunspec = reg_values[0] == 21365 and reg_values[1] == 28243
            if is_sunspec:
                logger.info("SunSpec-Marker 'SunS' erkannt – Wechselrichter ist SunSpec-kompatibel")
            else:
                logger.warning(
                    "Unerwartete Registerwerte: %d, %d (erwartet: 21365, 28243)",
                    reg_values[0],
                    reg_values[1],
                )

            return {
                "success": True,
                "registers": reg_values,
                "sunspec_valid": is_sunspec,
            }

        except Exception as e:
            logger.exception("Modbus-Fehler bei test_connection")
            return {"success": False, "error": str(e)}

        finally:
            client.close()
            logger.info("Modbus-Verbindung geschlossen")

    async def get_battery_status(self) -> dict:
        """Liest den SunSpec Storage Control Block (Model 124, int+SF).
        Fronius Verto Plus: Basisadresse 40362 (0-basiert) = Fronius Register 40363."""

        def to_int16(v: int) -> int:
            return v - 65536 if v > 32767 else v

        client = AsyncModbusTcpClient(self.host, port=self.port)

        try:
            connected = await client.connect()
            if not connected:
                logger.error("Modbus-Verbindung fehlgeschlagen")
                return {"success": False, "error": "Verbindung fehlgeschlagen"}

            result = await client.read_holding_registers(
                address=40363,
                count=26,
                device_id=self.slave_id,
            )

            if result.isError():
                logger.error("Fehler beim Lesen der Batterie-Register: %s", result)
                return {"success": False, "error": str(result)}

            regs = result.registers
            logger.info("Batterie-Register (40363-40388): %s", regs)

            # Sicherheitscheck: regs[0] sollte Model-ID 124 sein
            id_check = regs[0]
            if id_check != 124:
                logger.warning("Unerwartete Model-ID: %d (erwartet: 124)", id_check)

            # Werte und Scale Factors (Fronius Verto Plus Offsets)
            wchamax_watt = regs[2]           # Direkt in Watt, kein Scale Factor
            if wchamax_watt < 1000:
                wchamax_watt = 15000         # Fallback: 15 kW Inverter-Limit
            storctl_mod  = regs[5]
            minrsvpct_sf = to_int16(regs[21])
            minrsvpct    = regs[7] * (10 ** minrsvpct_sf)
            inoutwrte_sf = to_int16(regs[25])
            inwrte       = to_int16(regs[13]) * (10 ** inoutwrte_sf)
            outwrte      = to_int16(regs[12]) * (10 ** inoutwrte_sf)

            # Watt-Werte aus Prozent berechnen
            charge_limit_watt    = wchamax_watt * (inwrte  / 100)
            discharge_limit_watt = wchamax_watt * (outwrte / 100)

            data = {
                "success": True,
                "id_check": id_check,
                "wchamax_watt": wchamax_watt,
                "charge_limit_pct": inwrte,
                "discharge_limit_pct": outwrte,
                "charge_limit_watt": charge_limit_watt,
                "discharge_limit_watt": discharge_limit_watt,
                "reserve_pct": minrsvpct,
                "control_mode": storctl_mod,
            }
            logger.info("Batterie-Status: %s", data)
            return data

        except Exception as e:
            logger.exception("Modbus-Fehler bei get_battery_status")
            return {"success": False, "error": str(e)}

        finally:
            client.close()

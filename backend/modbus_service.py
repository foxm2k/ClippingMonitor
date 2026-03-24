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

    async def set_charge_limit(self, target_percentage: float) -> dict:
        """Setzt das Batterieladelimit (InWRte) ueber Modbus TCP.

        target_percentage: Ladelimit in Prozent (z.B. 50.0 fuer 50%).
        Liest den Scale Factor, berechnet den Rohwert und schreibt ihn.
        Liest anschliessend StorCtl_Mod und warnt, falls Ladelimit-Bit nicht aktiv.
        """

        def to_int16(v: int) -> int:
            return v - 65536 if v > 32767 else v

        client = AsyncModbusTcpClient(self.host, port=self.port)

        try:
            connected = await client.connect()
            if not connected:
                logger.error("Modbus-Verbindung fehlgeschlagen")
                return {"success": False, "error": "Verbindung fehlgeschlagen"}

            # 1. Scale Factor lesen (Register 40388, int16)
            sf_result = await client.read_holding_registers(
                address=40388,
                count=1,
                device_id=self.slave_id,
            )
            if sf_result.isError():
                logger.error("Fehler beim Lesen des Scale Factors: %s", sf_result)
                return {"success": False, "error": f"Scale Factor nicht lesbar: {sf_result}"}

            sf = to_int16(sf_result.registers[0])
            logger.info("InOutWRte_SF: %d", sf)

            # 2. Rohwert berechnen: raw_value = target_percentage * 10^(-sf)
            raw_value = int(round(target_percentage * (10 ** -sf)))
            logger.info(
                "Schreibe InWRte: %d (%.1f%% mit SF=%d)", raw_value, target_percentage, sf
            )

            # 3. Ladelimit schreiben (Register 40376, int16)
            #    int16 negativ → unsigned fuer Modbus-Protokoll
            write_value = raw_value if raw_value >= 0 else raw_value + 65536
            write_result = await client.write_register(
                address=40376,
                value=write_value,
                device_id=self.slave_id,
            )
            if write_result.isError():
                logger.error("Fehler beim Schreiben von InWRte: %s", write_result)
                return {"success": False, "error": f"InWRte Schreibfehler: {write_result}"}

            # 4. StorCtl_Mod lesen (Register 40368, uint16) und Zustand pruefen
            storctl_result = await client.read_holding_registers(
                address=40368,
                count=1,
                device_id=self.slave_id,
            )
            if storctl_result.isError():
                logger.error("Fehler beim Lesen von StorCtl_Mod: %s", storctl_result)
                return {"success": False, "error": f"StorCtl_Mod nicht lesbar: {storctl_result}"}

            storctl_mod = storctl_result.registers[0]
            logger.info("Aktueller StorCtl_Mod: %d", storctl_mod)

            # Bit 0 = Ladelimit aktiv → Modus muss 1 oder 3 sein
            charge_limit_active = bool(storctl_mod & 1)
            if not charge_limit_active:
                logger.warning(
                    "StorCtl_Mod=%d – Ladelimit-Bit ist NICHT aktiv! "
                    "Wert wurde geschrieben, aber der WR erzwingt ihn moeglicherweise nicht.",
                    storctl_mod,
                )

            return {
                "success": True,
                "charge_limit_pct": target_percentage,
                "raw_value_written": raw_value,
                "scale_factor": sf,
                "control_mode": storctl_mod,
                "charge_limit_active": charge_limit_active,
            }

        except Exception as e:
            logger.exception("Modbus-Fehler bei set_charge_limit")
            return {"success": False, "error": str(e)}

        finally:
            client.close()

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

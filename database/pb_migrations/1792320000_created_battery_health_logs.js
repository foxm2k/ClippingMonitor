/// <reference path="../pb_data/types.d.ts" />
migrate((app) => {
  const text = (name) => ({ name, type: "text" });
  const bool = (name) => ({ name, type: "bool" });
  const num = (name) => ({ name, type: "number" });

  const collection = new Collection({
    type: "base",
    name: "battery_health_logs",
    listRule: "",
    viewRule: "",
    createRule: "",
    updateRule: null,
    deleteRule: null,
    fields: [
      text("source"),
      text("battery_mode"),
      bool("battery_standby"),
      bool("backup_mode"),
      num("soc"),
      num("soh"),
      num("temp_cell"),
      num("temp_cell_min"),
      num("temp_cell_max"),
      num("temp_ambient"),
      num("temp_limit_min"),
      num("temp_limit_max"),
      num("voltage_dc"),
      num("current_dc"),
      num("cell_state"),
      num("status_bms"),
      num("status_battery"),
      num("stack_state_flags"),
      num("cell_voltage_min"),
      num("cell_voltage_max"),
      num("cell_spread_mv"),
      num("module_count"),
      num("capacity_wh"),
      num("lifetime_charged_kwh"),
      num("lifetime_discharged_kwh"),
      num("cycles"),
      text("inverter_state"),
      num("inverter_status_code"),
      num("inverter_error_code"),
      { name: "created", type: "autodate", onCreate: true, onUpdate: false },
      { name: "updated", type: "autodate", onCreate: true, onUpdate: true },
    ],
    indexes: [
      "CREATE INDEX idx_battery_health_logs_created ON battery_health_logs (created)",
    ],
  });

  return app.save(collection);
}, (app) => {
  return app.delete(app.findCollectionByNameOrId("battery_health_logs"));
});

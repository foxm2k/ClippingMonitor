/// <reference path="../pb_data/types.d.ts" />
migrate((app) => {
  const text = (name) => ({ name, type: "text" });

  const collection = new Collection({
    type: "base",
    name: "battery_events",
    listRule: "",
    viewRule: "",
    createRule: "",
    updateRule: null,
    deleteRule: null,
    fields: [
      text("category"),
      text("severity"),
      text("message"),
      text("old_value"),
      text("new_value"),
      { name: "created", type: "autodate", onCreate: true, onUpdate: false },
      { name: "updated", type: "autodate", onCreate: true, onUpdate: true },
    ],
    indexes: [
      "CREATE INDEX idx_battery_events_created ON battery_events (created)",
    ],
  });

  return app.save(collection);
}, (app) => {
  return app.delete(app.findCollectionByNameOrId("battery_events"));
});

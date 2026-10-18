/// <reference path="../pb_data/types.d.ts" />
// Zeitpunkt des Auftretens: bei gepufferten (nachgetragenen) Ereignissen
// weicht er vom PocketBase-"created" (Schreibzeitpunkt) ab.
const INDEX = "CREATE INDEX idx_battery_events_occurred_at ON battery_events (occurred_at)";

migrate((app) => {
  const collection = app.findCollectionByNameOrId("battery_events");

  collection.fields.add(new DateField({ name: "occurred_at" }));
  collection.indexes.push(INDEX);

  return app.save(collection);
}, (app) => {
  const collection = app.findCollectionByNameOrId("battery_events");

  collection.fields.removeByName("occurred_at");
  collection.indexes = collection.indexes.filter((idx) => !idx.includes("idx_battery_events_occurred_at"));

  return app.save(collection);
});

/// <reference path="../pb_data/types.d.ts" />
migrate((app) => {
  const collection = app.findCollectionByNameOrId("pbc_986744144")

  // update collection data
  unmarshal({
    "listRule": null,
    "updateRule": null
  }, collection)

  return app.save(collection)
}, (app) => {
  const collection = app.findCollectionByNameOrId("pbc_986744144")

  // update collection data
  unmarshal({
    "listRule": "",
    "updateRule": ""
  }, collection)

  return app.save(collection)
})

import "./telemetry"; // must be first: patches express/amqplib/http as they load
import { createApp, catalogPrices, wireSaga } from "./app";
import { rabbitBus } from "./bus";
import { OrderStore } from "./store";

async function main() {
  const store = new OrderStore(process.env.ORDERS_DB ?? ":memory:");
  const bus = await rabbitBus(process.env.RABBITMQ_URL ?? "amqp://localhost");
  await wireSaga(store, bus);
  const port = Number(process.env.PORT ?? 3001);
  createApp(store, bus, catalogPrices(process.env.CATALOG_URL ?? "http://localhost:8001")).listen(port, () =>
    console.log(`orders service on :${port}`),
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

import "./telemetry"; // must be first: patches express/http as they load
import { createGateway } from "./app";

const app = createGateway({
  orders: process.env.ORDERS_URL ?? "http://localhost:3001",
  catalog: process.env.CATALOG_URL ?? "http://localhost:8001",
  notifications: process.env.NOTIFICATIONS_URL ?? "http://localhost:8002",
});
const port = Number(process.env.PORT ?? 8080);
app.listen(port, () => console.log(`gateway on :${port}`));

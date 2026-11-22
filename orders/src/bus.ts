import amqp from "amqplib";

export const EXCHANGE = "shop.events";

export interface Bus {
  publish(routingKey: string, body: object): Promise<void>;
  subscribe(queue: string, keys: string[], handler: (key: string, body: any) => Promise<void>): Promise<void>;
  close(): Promise<void>;
}

/** RabbitMQ topic exchange. Messages are persistent and acked only after the handler succeeds. */
export async function rabbitBus(url: string): Promise<Bus> {
  const connection = await amqp.connect(url);
  const channel = await connection.createChannel();
  await channel.assertExchange(EXCHANGE, "topic", { durable: true });
  await channel.prefetch(10);
  return {
    async publish(routingKey, body) {
      channel.publish(EXCHANGE, routingKey, Buffer.from(JSON.stringify(body)), { persistent: true });
    },
    async subscribe(queue, keys, handler) {
      await channel.assertQueue(queue, { durable: true });
      for (const key of keys) await channel.bindQueue(queue, EXCHANGE, key);
      await channel.consume(queue, async (message) => {
        if (!message) return;
        try {
          await handler(message.fields.routingKey, JSON.parse(message.content.toString()));
          channel.ack(message);
        } catch (err) {
          console.error("handler failed, message will be redelivered", err);
          channel.nack(message, false, !message.fields.redelivered); // retry once, then drop
        }
      });
    },
    async close() {
      await channel.close();
      await connection.close();
    },
  };
}

/** In-memory bus for tests: delivers synchronously to subscribers. */
export function memoryBus(): Bus & { published: { key: string; body: any }[] } {
  const handlers: { keys: string[]; handler: (k: string, b: any) => Promise<void> }[] = [];
  const published: { key: string; body: any }[] = [];
  return {
    published,
    async publish(key, body) {
      published.push({ key, body });
      for (const h of handlers) if (h.keys.includes(key)) await h.handler(key, body);
    },
    async subscribe(_queue, keys, handler) {
      handlers.push({ keys, handler });
    },
    async close() {},
  };
}

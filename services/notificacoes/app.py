import json
import logging
import os
import time
import pika

# ─── Setup ────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ─── Email simulation ─────────────────────────────────────────────────────────

def simulate_email(data: dict):
    logger.info(json.dumps({
        "action": "email_sent",
        "to": data.get("usuario_email"),
        "subject": f"Confirmação de compra — {data.get('evento_id')}",
        "body": (
            f"Olá {data.get('usuario_nome')}, "
            f"sua compra de {data.get('quantidade')} ingresso(s) foi confirmada. "
            f"Total: R$ {data.get('valor_total'):.2f} via {data.get('metodo_pagamento')}."
        ),
        "compra_id": data.get("compra_id")
    }))


# ─── Message handler ──────────────────────────────────────────────────────────

def on_message(channel, method, properties, body):
    try:
        data = json.loads(body)
        logger.info(json.dumps({
            "action": "message_received",
            "compra_id": data.get("compra_id")
        }))
        simulate_email(data)
        channel.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        logger.error(json.dumps({
            "action": "message_error",
            "error": str(e),
            "body": body.decode()
        }))
        # Rejeita sem requeue para não travar a fila com mensagem corrompida
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)


# ─── Connection with retry ────────────────────────────────────────────────────

def connect_with_retry(retries: int = 10, delay: int = 3):
    url = os.getenv("RABBITMQ_URL")
    params = pika.URLParameters(url)

    for attempt in range(retries):
        try:
            connection = pika.BlockingConnection(params)
            logger.info(json.dumps({"action": "rabbitmq_connected"}))
            return connection
        except Exception as e:
            logger.warning(json.dumps({
                "action": "rabbitmq_retry",
                "attempt": attempt + 1,
                "error": str(e)
            }))
            time.sleep(delay)

    raise RuntimeError("Could not connect to RabbitMQ after retries")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    logger.info(json.dumps({"action": "service_starting"}))

    connection = connect_with_retry()
    channel = connection.channel()

    channel.queue_declare(queue="notificacoes", durable=True)

    # Processa uma mensagem por vez — não sobrecarrega o worker
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue="notificacoes", on_message_callback=on_message)

    logger.info(json.dumps({"action": "waiting_messages", "queue": "notificacoes"}))
    channel.start_consuming()


if __name__ == "__main__":
    main()
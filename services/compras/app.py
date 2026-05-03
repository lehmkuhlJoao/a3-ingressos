import logging
import json
import time
import os
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
import pika
from database import get_db, engine
from models import Usuario, Compra, Base
from prometheus_fastapi_instrumentator import Instrumentator

# ─── Setup ────────────────────────────────────────────────────────────────────

Base.metadata.create_all(bind=engine)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Compras Service")

Instrumentator().instrument(app).expose(app)

# ─── RabbitMQ ─────────────────────────────────────────────────────────────────

def publish_to_rabbitmq(message: dict, retries: int = 3):
    for attempt in range(retries):
        try:
            url = os.getenv("RABBITMQ_URL")
            params = pika.URLParameters(url)
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            channel.queue_declare(queue="notificacoes", durable=True)
            channel.basic_publish(
                exchange="",
                routing_key="notificacoes",
                body=json.dumps(message),
                properties=pika.BasicProperties(delivery_mode=2)
            )
            connection.close()
            logger.info(json.dumps({"action": "rabbitmq_published", "message": message}))
            return
        except Exception as e:
            logger.warning(json.dumps({"action": "rabbitmq_retry", "attempt": attempt + 1, "error": str(e)}))
            time.sleep(2)
    logger.error(json.dumps({"action": "rabbitmq_failed", "message": message}))


# ─── Schemas ──────────────────────────────────────────────────────────────────

class UsuarioCreate(BaseModel):
    nome: str
    email: str
    senha: str


class CompraCreate(BaseModel):
    transaction_id: str
    usuario_id: int
    evento_id: int
    quantidade: int
    metodo_pagamento: str


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/usuarios", status_code=201)
def criar_usuario(usuario: UsuarioCreate, db: Session = Depends(get_db)):
    existing = db.query(Usuario).filter(Usuario.email == usuario.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    novo_usuario = Usuario(**usuario.model_dump())
    db.add(novo_usuario)
    db.commit()
    db.refresh(novo_usuario)
    logger.info(json.dumps({"action": "create_usuario", "id": novo_usuario.id, "email": novo_usuario.email}))
    return novo_usuario


@app.get("/compras/{compra_id}")
def consultar_compra(compra_id: int, db: Session = Depends(get_db)):
    compra = db.query(Compra).filter(Compra.id == compra_id).first()
    if not compra:
        raise HTTPException(status_code=404, detail="Compra not found")
    return compra


@app.post("/compras", status_code=201)
def realizar_compra(compra: CompraCreate, db: Session = Depends(get_db)):

    # ── 1. Idempotência ──────────────────────────────────────────────────────
    existing = db.query(Compra).filter(Compra.transaction_id == compra.transaction_id).first()
    if existing:
        logger.info(json.dumps({"action": "compra_idempotent", "transaction_id": compra.transaction_id}))
        return existing

    # ── 2. Verifica usuário ──────────────────────────────────────────────────
    usuario = db.query(Usuario).filter(Usuario.id == compra.usuario_id).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario not found")

    # ── 3. Valida método de pagamento ────────────────────────────────────────
    metodos_validos = ["boleto", "pix", "cartao"]
    if compra.metodo_pagamento not in metodos_validos:
        raise HTTPException(status_code=400, detail=f"Metodo invalido. Use: {metodos_validos}")

    # ── 4. SELECT FOR UPDATE — busca e trava o evento ────────────────────────
    evento = db.execute(
        text("SELECT * FROM eventos WHERE id = :id FOR UPDATE"),
        {"id": compra.evento_id}
    ).fetchone()

    if not evento:
        raise HTTPException(status_code=404, detail="Evento not found")

    # ── 5. Verifica disponibilidade ──────────────────────────────────────────
    if evento.quantidade < compra.quantidade:
        raise HTTPException(status_code=409, detail="Ingressos insuficientes")

    # ── 6. Decrementa quantidade ─────────────────────────────────────────────
    db.execute(
        text("UPDATE eventos SET quantidade = quantidade - :qtd WHERE id = :id"),
        {"qtd": compra.quantidade, "id": compra.evento_id}
    )

    # ── 7. Simula pagamento ──────────────────────────────────────────────────
    valor_total = evento.valor * compra.quantidade
    logger.info(json.dumps({
        "action": "payment_processed",
        "metodo": compra.metodo_pagamento,
        "valor": valor_total
    }))

    # ── 8. Registra a compra e commit ────────────────────────────────────────
    nova_compra = Compra(
        transaction_id=compra.transaction_id,
        usuario_id=compra.usuario_id,
        evento_id=compra.evento_id,
        quantidade=compra.quantidade,
        valor_total=valor_total,
        metodo_pagamento=compra.metodo_pagamento,
        status="confirmado"
    )
    db.add(nova_compra)
    db.commit()
    db.refresh(nova_compra)

    logger.info(json.dumps({
        "action": "compra_confirmed",
        "compra_id": nova_compra.id,
        "transaction_id": compra.transaction_id,
        "evento_id": compra.evento_id
    }))

    # ── 9. Publica no RabbitMQ ───────────────────────────────────────────────
    publish_to_rabbitmq({
        "compra_id": nova_compra.id,
        "usuario_email": usuario.email,
        "usuario_nome": usuario.nome,
        "evento_id": compra.evento_id,
        "quantidade": compra.quantidade,
        "valor_total": valor_total,
        "metodo_pagamento": compra.metodo_pagamento
    })

    return nova_compra

import logging
import json
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database import get_db, engine
from models import Evento, Base
from prometheus_fastapi_instrumentator import Instrumentator

# ─── Setup ────────────────────────────────────────────────────────────────────

# Create tables on startup
Base.metadata.create_all(bind=engine)

# Structured logging (JSON format for observability)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Eventos Service")

Instrumentator().instrument(app).expose(app)

# ─── Schemas ──────────────────────────────────────────────────────────────────

class EventoCreate(BaseModel):
    nome: str
    data: str
    valor: float
    quantidade: int


class EventoUpdateQuantidade(BaseModel):
    quantidade: int


class EventoUpdateValor(BaseModel):
    valor: float


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/eventos")
def listar_eventos(db: Session = Depends(get_db)):
    eventos = db.query(Evento).all()
    logger.info(json.dumps({"action": "list_eventos", "count": len(eventos)}))
    return eventos


@app.post("/eventos", status_code=201)
def criar_evento(evento: EventoCreate, db: Session = Depends(get_db)):
    novo_evento = Evento(**evento.model_dump())
    db.add(novo_evento)
    db.commit()
    db.refresh(novo_evento)
    logger.info(json.dumps({"action": "create_evento", "id": novo_evento.id, "nome": novo_evento.nome}))
    return novo_evento


@app.put("/eventos/{evento_id}/quantidade")
def atualizar_quantidade(evento_id: int, body: EventoUpdateQuantidade, db: Session = Depends(get_db)):
    evento = db.query(Evento).filter(Evento.id == evento_id).first()
    if not evento:
        raise HTTPException(status_code=404, detail="Evento not found")
    evento.quantidade = body.quantidade
    db.commit()
    db.refresh(evento)
    logger.info(json.dumps({"action": "update_quantidade", "id": evento_id, "quantidade": body.quantidade}))
    return evento


@app.put("/eventos/{evento_id}/valor")
def atualizar_valor(evento_id: int, body: EventoUpdateValor, db: Session = Depends(get_db)):
    evento = db.query(Evento).filter(Evento.id == evento_id).first()
    if not evento:
        raise HTTPException(status_code=404, detail="Evento not found")
    evento.valor = body.valor
    db.commit()
    db.refresh(evento)
    logger.info(json.dumps({"action": "update_valor", "id": evento_id, "valor": body.valor}))
    return evento

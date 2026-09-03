# A3 Ingressos

Sistema de venda de ingressos para eventos, desenvolvido como projeto acadêmico (A3) com foco em **arquitetura distribuída**: decomposição em serviços, comunicação síncrona e assíncrona, controle de concorrência sobre um recurso compartilhado (estoque de ingressos), API gateway e observabilidade desde o início do desenvolvimento.

O domínio (compra de ingressos) é um veículo para demonstrar esses conceitos — não é um produto de ticketing completo. Pagamento é simulado e não há integração com gateways externos.

## Arquitetura

```
                        ┌──────────────┐
                        │   frontend   │  (nginx:alpine :8080)
                        └──────┬───────┘
                               │ HTTP/REST
                        ┌──────▼───────┐
                        │    nginx     │  API Gateway (:80)
                        └──┬────────┬──┘
                 ┌─────────▼──┐  ┌──▼──────────┐
                 │   eventos  │  │   compras   │
                 │  (FastAPI) │  │  (FastAPI)  │
                 └──────┬─────┘  └──┬───────┬──┘
                        │           │       │
                        └─────┬─────┘       │ publica
                              │              ▼
                        ┌─────▼─────┐  ┌───────────┐
                        │ postgres  │  │ rabbitmq  │
                        └───────────┘  └─────┬─────┘
                                              │ consome
                                        ┌─────▼──────┐
                                        │notificacoes│
                                        └────────────┘

        prometheus (:9090) ──scrape──> eventos, compras
        grafana (:3000) ──dashboards──> prometheus
```

| Componente | Papel |
|---|---|
| **frontend** | SPA estática (HTML/CSS/JS, sem framework) servida por um container nginx. Catálogo de eventos, checkout de compra, simulador de carga (dispara compras/leituras em série) e painel de logs de atividade. |
| **nginx** | API Gateway / reverse proxy na frente dos serviços `eventos` e `compras`. Roteia por path (`/eventos`, `/compras`, `/usuarios`) e trata CORS. |
| **eventos** | Microsserviço FastAPI dono da tabela `eventos`. Endpoints para listar, criar e atualizar quantidade/valor de um evento. |
| **compras** | Microsserviço FastAPI dono das tabelas `usuarios` e `compras`. Concentra a regra de negócio principal: criação de usuário e processamento da compra (validação, controle de estoque, registro e publicação do evento de confirmação). |
| **notificacoes** | Worker Python (não expõe HTTP) que consome a fila `notificacoes` do RabbitMQ e simula o envio de um e-mail de confirmação de compra. |
| **postgres** | Banco relacional único, usado por `eventos` e `compras`. |
| **rabbitmq** | Broker de mensagens, desacopla `compras` (produtor) de `notificacoes` (consumidor). |
| **prometheus** | Coleta métricas expostas por `eventos` e `compras` (`/metrics`). |
| **grafana** | Dashboards sobre as métricas coletadas pelo Prometheus. |

## Tecnologias

- **Backend**: Python 3.12, FastAPI, Uvicorn, SQLAlchemy 2.0, Pydantic v2
- **Banco de dados**: PostgreSQL 16
- **Mensageria**: RabbitMQ 3.13 (protocolo AMQP, cliente `pika`)
- **Frontend**: HTML, CSS e JavaScript puro, sem build step
- **Observabilidade**: Prometheus, Grafana, `prometheus-fastapi-instrumentator`
- **Infraestrutura**: Docker e Docker Compose, Nginx como API Gateway

## Como rodar localmente

Pré-requisitos: Docker e Docker Compose.

1. Crie um arquivo `.env` na raiz do projeto com as variáveis usadas pelo `docker-compose.yml`:

   ```env
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=postgres
   POSTGRES_DB=a3_ingressos

   RABBITMQ_USER=guest
   RABBITMQ_PASSWORD=guest

   GRAFANA_PASSWORD=admin
   ```

2. Suba os containers:

   ```bash
   docker compose up --build
   ```

3. Acesse:

   | Serviço | URL |
   |---|---|
   | Frontend | http://localhost:8080 |
   | API Gateway (nginx) | http://localhost:80 |
   | RabbitMQ Management | http://localhost:15672 |
   | Grafana | http://localhost:3000 |
   | Prometheus | http://localhost:9090 |

## Destaques técnicos

- **Controle de concorrência (`SELECT ... FOR UPDATE`)**: o endpoint de compra em `compras` trava a linha do evento no Postgres antes de checar disponibilidade e decrementar o estoque, dentro de uma única transação. Isso evita overselling quando múltiplas compras concorrentes disputam o mesmo evento.
- **Idempotência via `transaction_id`**: cada compra carrega um `transaction_id` único; se a mesma requisição for reenviada (retry de cliente, timeout de rede), a compra existente é retornada em vez de duplicada.
- **Comunicação assíncrona via RabbitMQ**: após confirmar uma compra, `compras` publica uma mensagem durável na fila `notificacoes`; o worker `notificacoes` consome de forma independente, desacoplando a confirmação da compra do envio da notificação. A publicação tem retry com backoff simples, e o consumo usa `basic_ack`/`basic_nack` manual com `prefetch_count=1`.
- **Observabilidade desde o início**: todos os serviços FastAPI expõem métricas Prometheus (`/metrics`) e logs estruturados em JSON, coletados pelo Prometheus e visualizados no Grafana — não foi algo adicionado depois, mas parte do desenho original.

## Execução na AWS (AWS Academy Lab)

Além do ambiente local via Docker Compose, o projeto também foi executado em um laboratório AWS Academy, mapeando os componentes containerizados para serviços equivalentes gerenciados pela AWS:

| Local (Docker) | AWS |
|---|---|
| Containers dos serviços (`eventos`, `compras`, `notificacoes`) | EC2 |
| RabbitMQ | SQS (como alternativa gerenciada de fila) |
| PostgreSQL | RDS |

O objetivo dessa etapa foi comparar, na prática, a operação de uma arquitetura distribuída self-hosted via containers com a mesma arquitetura usando serviços gerenciados de nuvem.

## Limitações conhecidas / possíveis melhorias futuras

- **Acoplamento via banco compartilhado**: `compras` acessa e trava a tabela `eventos` diretamente via SQL, em vez de chamar uma API do serviço `eventos`. Funciona, mas quebra o princípio de cada serviço ser dono exclusivo dos seus dados — numa versão futura, essa interação deveria passar por uma API (ou um padrão de reserva/saga).
- **Sem autenticação/autorização**: não há login, hash de senha, JWT ou qualquer controle de acesso nas APIs.
- **Sem testes automatizados**: o repositório não tem suíte de testes unitários ou de integração, o que é especialmente relevante no endpoint de compra, dado o controle de concorrência envolvido.
- **Sem dead-letter queue no RabbitMQ**: mensagens rejeitadas pelo consumidor (`basic_nack` sem requeue) são descartadas, sem fila para inspeção ou reprocessamento de falhas.
- **Sem migrations**: o schema é criado via `Base.metadata.create_all()` no startup de cada serviço, sem versionamento (ex.: Alembic) para evoluir o banco de forma controlada.

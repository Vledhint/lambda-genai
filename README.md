# lambda-genai

Función AWS Lambda que expone un asistente conversacional con IA generativa (RAG) mediante una API REST. El asistente responde en español, con un tono inspirado en el estilo de Martha Debayle, usando como contexto la información de una **Knowledge Base de Amazon Bedrock** y guardando el historial de cada conversación en **DynamoDB**.

## Arquitectura

```
Cliente ──POST /query──▶ API Gateway ──▶ Lambda (mmkg-genai-query)
                                              │
                          ┌───────────────────┼────────────────────┐
                          ▼                   ▼                    ▼
              Bedrock Knowledge Base   Bedrock Runtime       DynamoDB
                 (retrieve)            (converse, Nova Micro)  (mmkg-chat-history)
```

1. API Gateway recibe la petición `POST /query` (con CORS habilitado).
2. La Lambda recupera el historial de la sesión desde DynamoDB.
3. Según el tipo de pregunta:
   - **Pregunta sobre el historial** (p. ej. "hazme un resumen", "lo que hablamos"): responde solo con el historial, sin consultar la Knowledge Base.
   - **Pregunta de seguimiento** (p. ej. "cuéntame más", "sobre eso"): consulta la Knowledge Base con la última pregunta del usuario.
   - **Pregunta nueva**: consulta la Knowledge Base con la pregunta actual.
4. Envía contexto + historial + pregunta al modelo mediante la API `Converse` de Bedrock.
5. Guarda la pregunta (sin el contexto) y la respuesta en DynamoDB, conservando solo los últimos 5 intercambios.

## Estructura del proyecto

| Archivo | Descripción |
|---|---|
| `lambda_function.py` | Handler de la Lambda: detección de follow-ups, RAG, llamada al modelo e historial. |
| `template.yaml` | Plantilla AWS SAM: función Lambda, API Gateway y tabla DynamoDB. |
| `requirements.txt` | Dependencias de Python (`boto3`). |

## Requisitos

- Cuenta de AWS con acceso a Amazon Bedrock y al modelo `amazon.nova-micro-v1:0` habilitado.
- Una Knowledge Base de Bedrock creada (con su fuente de datos en S3).
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) y AWS CLI configurados.
- Python 3.12.

## Variables de entorno

| Variable | Valor por defecto | Descripción |
|---|---|---|
| `KB_ID` | `W9RCRTPF5H` | ID de la Knowledge Base de Bedrock. |
| `MODEL_ID` | `amazon.nova-micro-v1:0` | Modelo de Bedrock usado para generar respuestas. |
| `TABLE_NAME` | `mmkg-chat-history` | Tabla DynamoDB para el historial de chat. |
| `AWS_REGION` | `us-east-1` | Región de AWS (la define Lambda automáticamente). |

Los valores se configuran en la sección `Globals` de `template.yaml`.

## Despliegue

```bash
sam build
sam deploy --guided
```

Al terminar, SAM muestra el output `ApiUrl` con la URL del endpoint:

```
https://<api-id>.execute-api.<region>.amazonaws.com/Prod/query
```

Recursos creados:

- **Lambda** `mmkg-genai-query` (256 MB, timeout 30 s) con permisos `bedrock:Retrieve`, `bedrock:InvokeModel`, `bedrock:Converse`, `dynamodb:GetItem` y `dynamodb:PutItem`.
- **API Gateway** con el endpoint `POST /query`.
- **DynamoDB** `mmkg-chat-history` (on-demand, clave `session_id`, TTL sobre el atributo `ttl`).

## Uso de la API

**Petición**

```bash
curl -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -d '{"query": "¿Qué tips me das para organizar mi semana?", "session_id": "usuario-123"}'
```

| Campo | Requerido | Descripción |
|---|---|---|
| `query` | Sí | Pregunta del usuario. |
| `session_id` | No | Identificador de la conversación. Por defecto `"default"`. |

**Respuesta exitosa (200)**

```json
{
  "respuesta": "<p>¡Qué bueno que me preguntas eso! ...</p>",
  "session_id": "usuario-123"
}
```

La respuesta viene formateada en **HTML** para mostrarse directamente en un frontend.

**Error (400)** — cuando falta `query`:

```json
{ "error": "Campo 'query' requerido" }
```

> Usa un `session_id` distinto por usuario/conversación; de lo contrario todas las peticiones comparten el historial de la sesión `default`.

## Notas

- El historial se limita a los últimos 5 pares pregunta/respuesta (`MAX_HISTORY`).
- La tabla tiene TTL habilitado sobre el atributo `ttl`, pero la función actualmente no escribe ese atributo, por lo que los registros no expiran automáticamente.
- El prompt de sistema (`SYSTEM_PROMPT`) define la personalidad y reglas de formato del asistente; ajústalo en `lambda_function.py`.

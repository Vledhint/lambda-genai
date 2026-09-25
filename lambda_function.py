import json
import os
import boto3
from datetime import datetime, timezone

KB_ID = os.environ.get("KB_ID", "W9RCRTPF5H")
MODEL_ID = os.environ.get("MODEL_ID", "amazon.nova-micro-v1:0")
REGION = os.environ.get("AWS_REGION", "us-east-1")
TABLE_NAME = os.environ.get("TABLE_NAME", "mmkg-chat-history")
MAX_HISTORY = 5

kb_client = boto3.client("bedrock-agent-runtime", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST,OPTIONS"
}

SYSTEM_PROMPT = """
Eres una asistente virtual con la personalidad, el tono y el estilo de Martha Debayle: 
la conductora, empresaria y figura pública mexicana conocida por su calidez directa, 
su entusiasmo genuino, su amor por los detalles prácticos, y su forma de hacer sentir 
especial a cada persona con quien habla.

- Hablas con energía, calidez y seguridad. Nunca suenas robótica ni fría.
- Eres directa pero empática — como Martha en entrevista: escuchas, conectas y orientas.
- Usas expresiones naturales del español mexicano: "¡Qué bueno que me preguntas eso!", 
  "Mira, te cuento...", "A ver, esto es importantísimo...", "¡Exacto!"
- Mezclas lo profesional con lo personal — como haría Martha al entrevistar a alguien 
  que admira su trabajo.
- Ocasionalmente usas algún anglicismo natural ("tips", "lifestyle", "follow-up") sin 
  forzarlo, tal como Martha lo hace en sus programas.

Estás en una conversación con una cuentahabiente/fan de Martha. 
Esta persona te admira, confía en ti y viene a ti buscando orientación, consejos o 
respuestas. Tu rol es recibirla con la calidez con la que Martha recibiría a alguien 
especial en su programa — hacerla sentir vista, escuchada y empoderada.

1. Saluda con entusiasmo genuino al inicio de la conversación.
2. Cuando no sepas algo, dilo con gracia y honestidad — Martha nunca finge saber lo 
   que no sabe.
3. Da respuestas concretas, cortas y accionables — Martha odia el relleno, ama los tips útiles y concretos.
4. Si el tema lo permite, comparte una perspectiva personal (como haría Martha: 
   "Yo siempre digo que...").
5. Cierra cada respuesta con energía positiva o una pequeña pregunta de seguimiento 
   que invite a continuar la conversación.
6. Mantén el tono de entrevista: tú eres la conductora, ella es la invitada especial.
7. Formateas todo el contenido en html para hacerlo más ameno y fácil de leer, usando negritas, listas, emojis o cualquier recurso que haga la respuesta más atractiva y cercana, como lo haría Martha en sus programas.
8. No dar respuestas con formato de markdown, sino en texto enriquecido para que sea más atractivo y cercano, como lo haría Martha en sus programas.
9. No respondas con texto para markdown, sino con texto humanamente legible para que noaparezcan caracteres raros en la conversación
10. Limita tus respuestas al tema de la pregunta, historial y contexto proporcionado, sin desviarte a temas no relacionados o información adicional que no sea relevante para la pregunta.
11. Nunca respondas con los IDs de los posts o con información técnica del origen de los datos. Siempre responde como si fueras Martha, con naturalidad y calidez, sin revelar que eres una asistente virtual.

- No hablas de forma genérica o aburrida.
- No das listas interminables sin contexto.
- No usas lenguaje corporativo frío.
- No finges ser Martha literalmente — eres una asistente *inspirada* en su estilo.
- No te desvías del contexto proporcionado para responder a la pregunta
- No des información del origen de los datos ni información de posts, ni menciones que eres una IA. Siempre responde como si fueras Martha, con naturalidad y calidez, sin revelar que eres una asistente virtual.
- No respondas que eres una asistente virual
- Dar respuestas extendidas, busca dar respuestas concretas y accionables, evitando el relleno de información irrelevante o redundante.
"""


def get_history(session_id: str) -> list:
    resp = table.get_item(Key={"session_id": session_id})
    return resp.get("Item", {}).get("messages", [])


def save_history(session_id: str, messages: list):
    # Mantener solo los últimos MAX_HISTORY pares (user + assistant)
    trimmed = messages[-(MAX_HISTORY * 2):]
    table.put_item(Item={
        "session_id": session_id,
        "messages": trimmed,
        "updated_at": datetime.now(timezone.utc).isoformat()
    })


FOLLOWUP_KEYWORDS = [
    "anterior", "lo que dijiste", "lo que mencionaste", "más detalle", "amplía",
    "ampliar", "cuéntame más", "explica más", "el primero", "el segundo",
    "eso que dijiste", "de eso", "sobre eso", "sigue", "continúa", "más sobre",
    "ampliame", "amplíame", "recuérdame", "recuerda", "resumen", "resumeme",
    "resúmeme", "hemos hablado", "conversación anterior", "última conversación",
    "me recerdas", "me recuerdas", "lo anterior", "lo mismo", "ese tema",
    "ese punto", "lo que hablamos", "nuestra conversación"
]

# Preguntas que hacen referencia al historial — NO deben consultar KB
HISTORY_ONLY_KEYWORDS = [
    "resumen", "resumeme", "resúmeme", "hemos hablado", "conversación anterior",
    "última conversación", "recuérdame", "recuerda", "me recerdas", "me recuerdas",
    "lo que hablamos", "nuestra conversación", "contexto de la"
]


def is_followup(pregunta: str) -> bool:
    p = pregunta.lower()
    return any(kw in p for kw in FOLLOWUP_KEYWORDS)


def is_history_only(pregunta: str) -> bool:
    """Preguntas sobre el historial que no deben consultar KB"""
    p = pregunta.lower()
    return any(kw in p for kw in HISTORY_ONLY_KEYWORDS)


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    body = json.loads(event.get("body") or "{}")
    pregunta = body.get("query")
    session_id = body.get("session_id", "default")

    if not pregunta:
        return {"statusCode": 400, "headers": CORS_HEADERS, "body": json.dumps({"error": "Campo 'query' requerido"})}

    history = get_history(session_id)

    # Preguntas sobre el historial: no consultar KB, solo usar historial
    if is_history_only(pregunta) and history:
        user_message = {"role": "user", "content": [{"text": pregunta}]}
        messages = history + [user_message]
        fuentes = []
    else:
        # En follow-ups, buscar KB con el último tema del usuario; si no, usar la pregunta actual
        kb_query = pregunta
        if is_followup(pregunta) and history:
            for msg in reversed(history):
                if msg["role"] == "user":
                    kb_query = msg["content"][0]["text"]
                    break

        retrieval = kb_client.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": kb_query}
        )
        resultados = retrieval["retrievalResults"]
        contexto = "\n\n".join([r["content"]["text"] for r in resultados])
        fuentes = list({r["location"]["s3Location"]["uri"].split("/")[-1] for r in resultados})
        user_message = {"role": "user", "content": [{"text": f"Contexto:\n{contexto}\n\nPregunta: {pregunta}"}]}
        messages = history + [user_message]

    resp = bedrock.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=messages,
        inferenceConfig={"maxTokens": 1024}
    )

    answer = resp["output"]["message"]["content"][0]["text"]

    clean_user_message = {"role": "user", "content": [{"text": pregunta}]}
    assistant_message = {"role": "assistant", "content": [{"text": answer}]}
    save_history(session_id, history + [clean_user_message, assistant_message])

    return {
        "statusCode": 200,
        "headers": CORS_HEADERS,
        "body": json.dumps({"respuesta": answer, "session_id": session_id}, ensure_ascii=False)
    }

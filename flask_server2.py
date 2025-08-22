# python -m venv .venv
# .venv\Scripts\activate
# pip install -r requirements.txt

# pip install Flask
# pip install llama-index-llms-ollama llama-index-embeddings-huggingface
# pip install rapidfuzz
# pip install textblob
# python flask_server2.py


# sqlite3 chat_logs.db
# .tables


from flask import Flask, request, jsonify
from llama_index.core import Document, VectorStoreIndex
from llama_index.llms.ollama import Ollama
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from rapidfuzz import fuzz, process
from textblob import TextBlob
import json
import os
import sqlite3

app = Flask(__name__)

# ✅ Initialize DB if not exists
def init_db():
    conn = sqlite3.connect("chat_logs.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT,
            question TEXT,
            answer TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

# ✅ Save chat to database
def save_chat_to_db(user_id, user_message, bot_response):
    try:
        conn = sqlite3.connect("chat_logs.db")
        c = conn.cursor()
        c.execute(
            "INSERT INTO chat_logs (phone, question, answer) VALUES (?, ?, ?)",
            (user_id, user_message, bot_response)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print("❌ Failed to log chat:", e)

# 🔁 Initialize DB on startup
init_db()

# ✅ Load JSONL data
def load_jsonl(path):
    documents = []
    if not os.path.exists(path):
        raise FileNotFoundError(f"❌ File not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            data = json.loads(line)
            q = data.get("question", "").strip()
            a = data.get("answer", "").strip()
            if q and a:
                content = f"Q: {q}\nA: {a}"
                documents.append(Document(text=content))
    return documents

# ✅ Model & Index Initialization
jsonl_path = "data/modified_data.jsonl"
documents = load_jsonl(jsonl_path)

llm = Ollama(
    model="gemma:2b",  #Models: gemma:2b, gemma:7b,llama3.1:8b,mistral:7b,phi3:3.8b
    temperature=0.4,
    max_tokens=400,
    system_prompt=(
        "You are a helpful and friendly resort assistant chatbot.\n\n"
        "First, the system checks if the user's question exactly matches a known question from the Q&A list. "
        "Your job is to identify important keywords in the user's question and select the most relevant and accurate answer from the provided context.\n"
        "Rules:\n"
        "1. If the user's question exactly matches a known Q&A, the full answer is returned directly. Do not respond in that case.\n"
        "2. If there is no exact match, analyze the keywords and respond based on the resort's knowledge base or provided context.\n\n"
        "Guidelines:\n"
        "- Answer clearly, accurately, and politely. Give all the relevant details the guest may find useful.\n"
        "- Avoid repeating the question or providing explanations.\n"
        "- Do not say things like: “Sure”, “ here's the answer”, “ here's the answer to your question”, “According to the context”, “The context says”, “As an AI”, or anything similar.\n"
        "- Never reword or change a matched answer.\n"
        "- Only respond when no exact match is found.\n\n"
        "Tone:\n"
        "- Be natural, helpful, and professional.\n"
        "- Use direct, complete sentences."
    )
)

embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en")
index = VectorStoreIndex.from_documents(documents, embed_model=embed_model)
query_engine = index.as_query_engine(similarity_top_k=5, llm=llm)

# ✅ Health check
@app.route("/", methods=["GET"])
def home():
    return "✅ Resort chatbot server is running"

# ✅ Main query route
@app.route("/query", methods=["POST"])
def query():
    data = request.get_json(silent=True)
    if not data or "question" not in data:
        return jsonify({"error": "Missing or invalid 'question' field"}), 400

    question = data["question"]
    user_phone = data.get("phone", "unknown")
    cleaned_question = question.lower().strip()
  

    # 🧠 Fuzzy match for known Qs
    with open(jsonl_path, "r", encoding="utf-8") as f:
        qa_pairs = [json.loads(line) for line in f if line.strip()]

    known_questions = [item["question"].lower().strip() for item in qa_pairs]
    match_data = process.extractOne(cleaned_question, known_questions, scorer=fuzz.token_sort_ratio)
    match, score = match_data[0], match_data[1]

    if score > 80:
        print(f"🔍 Corrected spelling: '{cleaned_question}' → '{match}' (score: {score})")
        cleaned_question = match

    try:
        retrieved_nodes = query_engine.retrieve(cleaned_question)
        if not retrieved_nodes:
            fallback = "Sorry, Please Dial '0' from your room phone, our staff will assist you."
            save_chat_to_db(user_phone, cleaned_question, fallback)
            return jsonify({"answer": fallback})

        # 📝 
        best_context = retrieved_nodes[0].get_content()

        # 🚫 Reject if context doesn't contain the question words (e.g. for gibberish)
        if not any(word in best_context.lower() for word in cleaned_question.split()):
            fallback = "From your room phone, Please dial '0' for the front desk, our team is ready to assist you."
            save_chat_to_db(user_phone, cleaned_question, fallback)
            return jsonify({"answer": fallback})

        # 🚫 Also block if context doesn't start with expected structure
        if not best_context.lower().startswith("q:"):
            fallback = "From your room phone, Please dial '0' for the front desk, our team is ready to assist you."
            save_chat_to_db(user_phone, cleaned_question, fallback)
            return jsonify({"answer": fallback})


        custom_prompt = (
            f"You are a helpful and polite hotel assistant. Respond briefly and clearly using only the provided context.\n"
            f"If the context does not contain the answer, say: \"I'm sorry, Please contact front desk.\"\n\n"
            f"Important Rules:\n"
            f"- Use a friendly and professional tone.\n"
            f"- Answer in 2 or 3 clear sentences.\n"
            f"- Answer complete and clear sentences.\n"
            f"- Do NOT repeat or rephrase the question.\n"
            f"- Do NOT say things like: 'the context says', 'according to the context', 'as an AI', 'sure', or 'the answer is'.\n"
            f"- Do NOT explain anything—only give a direct answer if available in the context.\n\n"
            f"Based on the following context, answer the question clearly and naturally in one or two lines.\n\n"
            f"Context: {best_context}\n\n"
            f"User Question: {cleaned_question}\n\n"
            f"Question: {cleaned_question}\n\n"
            "Answer:"
        )

        model_reply = llm.complete(custom_prompt)
        final_reply = str(model_reply).strip()

        vague_responses = [
            "the context does not mention",
            "as it is mentioned in the context.",
            "the context mentions",
            "the context indicates that",
            "please refer to the context",
            "cannot answer",
            "as per the context",
            "not enough information",
            "i'm sorry",
            "as an ai",
            "according to the context",
            "context confirms",
            "the context says"
        ]

        if any(phrase in final_reply.lower() for phrase in vague_responses):
            final_reply = "From your room phone, Please dial '0' for the front desk, our team is ready to assist you."

        print(f"📥 Question: {cleaned_question}")
        print(f"📤 Answer: {final_reply}")

        save_chat_to_db(user_phone, cleaned_question, final_reply)

        return jsonify({"answer": final_reply})

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return jsonify({"answer": "Sorry, something went wrong on the server."}), 500

# ✅ Optional alias
@app.route("/chat", methods=["POST"])
def chat():
    return query()

if __name__ == "__main__":
    app.run(debug=True, port=5000)

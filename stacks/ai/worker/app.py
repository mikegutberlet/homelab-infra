"""Small HTTP front for LangChain and CrewAI jobs, called from n8n."""
import os

from fastapi import FastAPI
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

app = FastAPI()

llm = ChatOpenAI(
    base_url=os.environ["LLM_API_BASE_URL"],
    api_key=os.environ["LLM_API_KEY"],
)


class Prompt(BaseModel):
    text: str


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/ask")
def ask(prompt: Prompt):
    return {"answer": llm.invoke(prompt.text).content}

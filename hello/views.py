from django.shortcuts import render
from django.http import HttpResponse, JsonResponse

from .models import Question

import pandas as pd
import openai
import numpy as np

from resemble import Resemble

import boto3

Resemble.api_key('0vWhLtB2fmjVIE0Nuzic5wtt')
openai.api_key = "sk-DOiDZHHE1f1tvxnO5zs103vHelanA6BVBVO44cN7"

COMPLETIONS_MODEL = "text-davinci-002"

MODEL_NAME = "curie"

DOC_EMBEDDINGS_MODEL = f"text-search-{MODEL_NAME}-doc-001"
QUERY_EMBEDDINGS_MODEL = f"text-search-{MODEL_NAME}-query-001"

MAX_SECTION_LEN = 500
SEPARATOR = "\n* "
separator_len = 3

COMPLETIONS_API_PARAMS = {
    # We use temperature of 0.0 because it gives the most predictable, factual answer.
    "temperature": 0.0,
    "max_tokens": 75,
    "model": COMPLETIONS_MODEL,
}

def get_embedding(text: str, model: str) -> list[float]:
    result = openai.Embedding.create(
      model=model,
      input=text
    )
    return result["data"][0]["embedding"]

def get_doc_embedding(text: str) -> list[float]:
    return get_embedding(text, DOC_EMBEDDINGS_MODEL)

def get_query_embedding(text: str) -> list[float]:
    return get_embedding(text, QUERY_EMBEDDINGS_MODEL)

def vector_similarity(x: list[float], y: list[float]) -> float:
    """
    We could use cosine similarity or dot product to calculate the similarity between vectors.
    In practice, we have found it makes little difference.
    """
    return np.dot(np.array(x), np.array(y))

def order_document_sections_by_query_similarity(query: str, contexts: dict[(str, str), np.array]) -> list[(float, (str, str))]:
    """
    Find the query embedding for the supplied query, and compare it against all of the pre-calculated document embeddings
    to find the most relevant sections.

    Return the list of document sections, sorted by relevance in descending order.
    """
    query_embedding = get_query_embedding(query)

    document_similarities = sorted([
        (vector_similarity(query_embedding, doc_embedding), doc_index) for doc_index, doc_embedding in contexts.items()
    ], reverse=True)

    return document_similarities

def load_embeddings(fname: str) -> dict[tuple[str, str], list[float]]:
    """
    Read the document embeddings and their keys from a CSV.

    fname is the path to a CSV with exactly these named columns:
        "title", "0", "1", ... up to the length of the embedding vectors.
    """

    df = pd.read_csv(fname, header=0)
    max_dim = max([int(c) for c in df.columns if c != "title"])
    return {
           (r.title): [r[str(i)] for i in range(max_dim + 1)] for _, r in df.iterrows()
    }

def construct_prompt(question: str, context_embeddings: dict, df: pd.DataFrame) -> str:
    """
    Fetch relevant
    """
    most_relevant_document_sections = order_document_sections_by_query_similarity(question, context_embeddings)

    chosen_sections = []
    chosen_sections_len = 0
    chosen_sections_indexes = []

    for _, section_index in most_relevant_document_sections:
        document_section = df.loc[section_index]

        chosen_sections_len += document_section.tokens + separator_len
        if chosen_sections_len > MAX_SECTION_LEN:
            break

        chosen_sections.append(SEPARATOR + document_section.content)
        chosen_sections_indexes.append(str(section_index))

    header = """Answer the question as truthfully as possible using the provided context, and if the answer is not contained within the text below, say "I don't know."\n\nContext:\n"""
    header = """Context:\n"""

    return header + "".join(chosen_sections) + "\n\n Q: " + question + "\n A:"

def answer_query_with_context(
    query: str,
    df: pd.DataFrame,
    document_embeddings: dict[(str, str), np.array],
) -> str:
    prompt = construct_prompt(
        query,
        document_embeddings,
        df
    )

    print("===\n", prompt)

    response = openai.Completion.create(
                prompt=prompt,
                **COMPLETIONS_API_PARAMS
            )

    return response["choices"][0]["text"].strip(" \n")

def index(request):
    return render(request, "index.html")

def ask(request):
    question = request.POST.get("question", "")

    previous_question = Question.objects.filter(question=question).first()
    previous_answer = previous_question.answer if previous_question else None

    if previous_answer:
        previous_question.ask_count = previous_question.ask_count + 1
        previous_question.save()
        return render(request, "answer.html", { "answer": previous_answer, "question": question, "audio_src_url": previous_question.audio_src_url })

    s3 = boto3.client(
        's3',
        aws_access_key_id="AKIAYXDBVZ7BCW3GF67W",
        aws_secret_access_key="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"
    )

    s3.download_file('askbook', 'pages.csv', 'pages.csv')
    s3.download_file('askbook', 'embeddings.csv', 'embeddings.csv')

    df = pd.read_csv('pages.csv')
    df = df.set_index(["title"])

    document_embeddings = load_embeddings('embeddings.csv')

    answer = answer_query_with_context(question, df, document_embeddings)
    print(answer)

    question = Question(question=question, answer=answer)
    question.save()

    project_uuid = '6314e4df'
    voice_uuid = '0eb3a3f1'

    response = Resemble.v2.clips.create_sync(
        project_uuid,
        voice_uuid,
        answer,
        title=None,
        sample_rate=None,
        output_format=None,
        precision=None,
        include_timestamps=None,
        is_public=None,
        is_archived=None,
        raw=None
    )

    question.audio_src_url = response['item']['audio_src']
    question.save()

    return render(request, "answer.html", { "answer": answer, "question": question.question, "audio_src_url": question.audio_src_url })

def db(request):
    questions = Question.objects.all().order_by('-ask_count')

    return render(request, "db.html", { "questions": questions })

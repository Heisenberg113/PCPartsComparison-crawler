import os 
import sys
import logging
from langchain_google_genai import GoogleGenerativeAIEmbeddings

class GoogleEmbeddingModelMethod:


    def __init__(self, model_name: str = "gemini-embedding-2-preview"):
        if not os.getenv("GOOGLE_API_KEY"):
            logging.error("Missing GOOGLE_API_KEY environment variable. Exiting...")
            sys.exit(1)

        if not os.getenv("LANGSMITH_TRACING"):
            os.environ["LANGSMITH_TRACING"] = "true"

        if not os.getenv("LANGSMITH_API_KEY"):
            logging.error("Missing LANGSMITH_API_KEY environment variable. Exiting...")
            sys.exit(1)

        self.__model_name = model_name

    def generate_embedding(self, input: str) -> list[float]:
        embeddings = GoogleGenerativeAIEmbeddings(
            model=self.__model_name,
            output_dimensionality=1024
        )

        vector = embeddings.embed_query(input)

        print(f"Vector length: {len(vector)}")

        return vector
    
    def generate_embeddings(self, inputs: list[str]) -> list[list[float]]:
        embeddings = GoogleGenerativeAIEmbeddings(
            model=self.__model_name,
            output_dimensionality=1024
        )

        vectors = embeddings.embed_documents(inputs)

        print(f"Vectors length: {len(vectors)}")

        return vectors

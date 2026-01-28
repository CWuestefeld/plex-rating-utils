import os
import json
import logging
from dotenv import load_dotenv
from litellm import completion

# Load the keys from .env into the system environment
load_dotenv()

class AIInterface:
    def __init__(self, model_name="gemini/gemini-2.5-flash"):
        self.model_name = model_name
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.logger = logging.getLogger(__name__)
        
        if not self.api_key:
            self.logger.error("No API key found in .env file!")

    def _call_llm(self, prompt):
        """Internal helper to manage the LiteLLM call."""
        try:
            response = completion(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                api_key=self.api_key
            )
            raw_content = response.choices[0].message.content
            # Strip Markdown formatting if the AI gets chatty
            return raw_content.strip().replace('```json', '').replace('```', '')
        except Exception as e:
            self.logger.error(f"AI Error: {e}")
            return None

    def resolve_unmatched_countries(self, messy_countries):
        """
        Takes a list of strings and returns a dictionary mapping 
        the messy string to a canonical ISO name.
        """
        if not messy_countries:
            return {}

        prompt = f"""
        Act as a music librarian. I have a list of 'country' strings from a music database that are messy or non-standard.
        Map each string to its canonical, English ISO-3166 country name.
        
        Rules:
        1. Return ONLY a JSON object.
        2. The key must be the original string.
        3. The value must be the canonical name (e.g., 'United States' instead of 'USA').
        4. If it is already canonical, keep it as is.
        5. If the string is not a country, use null.

        List: {messy_countries}
        """
        
        result_str = self._call_llm(prompt)
        try:
            return json.loads(result_str) if result_str else {}
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse AI JSON: {e}")
            return {}
import os
import unittest

from pdf_to_jats.llm.azure_openai_client import AzureOpenAIClient


class AzureOpenAIClientTests(unittest.TestCase):
    def test_uses_azure_deployment_environment_variable(self):
        old = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        os.environ["AZURE_OPENAI_DEPLOYMENT"] = "my-prod-deployment"
        try:
            client = AzureOpenAIClient(endpoint="https://example-resource.openai.azure.com", api_key="secret")
            self.assertEqual(client.model, "my-prod-deployment")
        finally:
            if old is None:
                os.environ.pop("AZURE_OPENAI_DEPLOYMENT", None)
            else:
                os.environ["AZURE_OPENAI_DEPLOYMENT"] = old

    def test_azure_url_uses_deployment_path_and_api_version(self):
        client = AzureOpenAIClient(
            model="my-prod-deployment",
            endpoint="https://example-resource.openai.azure.com",
            api_key="secret",
        )
        request = client._build_request("hello")
        self.assertEqual(
            request.full_url,
            "https://example-resource.openai.azure.com/openai/deployments/my-prod-deployment/responses?api-version=2025-04-01-preview",
        )


if __name__ == "__main__":
    unittest.main()

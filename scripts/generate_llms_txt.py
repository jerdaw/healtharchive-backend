import os

CORE_DOCS = [
    "AGENTS.md",
    "README.md",
    "docs/architecture.md",
    "docs/documentation-guidelines.md",
]


def generate_llms_txt():
    content = "# HealthArchive - Developer Assistant Context\n\n"
    content += "This file provides high-level context for automated developer assistants working on HealthArchive.\n\n"

    for doc_path in CORE_DOCS:
        if os.path.exists(doc_path):
            with open(doc_path, "r") as f:
                content += f"## {doc_path}\n\n"
                content += f.read()
                content += "\n\n---\n\n"

    with open("docs/llms.txt", "w") as f:
        f.write(content)
    print("Generated docs/llms.txt")


if __name__ == "__main__":
    generate_llms_txt()

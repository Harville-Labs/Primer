# Architecture decisions

| Number | Decision | Status |
| --- | --- | --- |
| [0001](0001-reading-themes.md) | Keep light and dark reading themes | Accepted |
| [0002](0002-knowledge-graph-in-neo4j.md) | Keep a knowledge graph in Neo4j, owned by Retrieval | Accepted; superseded in part by 0014 |
| [0003](0003-bundle-postgresql.md) | Offer a bundled PostgreSQL in the chart | Accepted |
| [0004](0004-qdrant-only.md) | Keep vectors in Qdrant, and only Qdrant | Accepted |
| [0005](0005-lab-local-research-model.md) | Let Lab load a small open model of its own | Accepted; superseded in part by 0007 |
| [0006](0006-library-wide-questions-from-passages.md) | Answer library-wide questions from passages chosen across communities | Accepted |
| [0007](0007-lab-saes-as-weights-only-pickles.md) | Open Qwen-Scope's SAEs as weights-only pickles | Accepted |
| [0008](0008-lab-saves-experiments-in-postgresql.md) | Keep Lab's saved experiments in the shared PostgreSQL | Accepted |
| [0009](0009-lab-attribution-graphs-in-their-own-environment.md) | Build Lab's attribution graphs in an environment of their own | Accepted |
| [0010](0010-studio-image-generation.md) | Make images in Studio, through a backend the operator brings | Accepted |
| [0011](0011-bundle-neo4j-by-default.md) | Run a Neo4j in every chart release by default | Superseded by 0014 |
| [0012](0012-bundle-a-search-mcp-server.md) | Offer a bundled web search MCP server, off by default | Accepted |
| [0013](0013-lab-loads-multimodal-checkpoints.md) | Load a multimodal checkpoint as the model it is | Accepted |
| [0014](0014-knowledge-graph-is-always-on.md) | Make the knowledge graph part of every deployment | Accepted |
| [0015](0015-bundle-llama-cpp-for-embeddings-and-reranking.md) | Bundle llama.cpp for embeddings and reranking, and refuse a changed embedder | Accepted |
| [0016](0016-store-uploads-in-a-directory-or-s3.md) | Store uploads in a directory or in S3, and nothing else | Accepted |

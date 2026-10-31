"""HTTP plumbing shared by Primer's FastAPI services.

Deliberately empty of re-exports. Import from the module that defines the
name - `primer_service.db`, `primer_service.errors`, `primer_service.durable`,
`primer_service.identity` - so that reading an import tells you where to go
and look.
"""

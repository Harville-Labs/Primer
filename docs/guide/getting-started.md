# Getting started

## Signing in

Primer does not have its own username and password. It sits behind an
identity provider you already run — Google Workspace, Entra ID, Keycloak,
anything that speaks OIDC — and trusts the identity that arrives with your
request.

For a single-user install, authentication can be switched off entirely. In
that mode every request is the same fixed local user, which is exactly right
for a laptop and exactly wrong for anything shared. See
[Configuration](../operations/configuration.md).

## Your first library

A **library** is a private collection of sources on one subject. Everything
in Primer hangs off a library: documents are uploaded into one, and every
question is asked of one.

Start with a narrow library rather than a broad one. A library holding forty
papers on a single topic gives sharper answers than one holding four hundred
on nine, because retrieval has fewer near-misses to choose between.

## Adding sources

Upload the documents you want to be able to ask about. Each upload is
processed in the background: parsed, split into passages, and indexed.
A document reports its progress while that happens, and becomes searchable
when it reaches `ready`.

Processing takes seconds for a short text file and longer for a large PDF
with images, because reading text out of images is slower than reading it
out of a text layer.

## Asking questions

Ask a question of a library, and Primer retrieves the passages most likely
to answer it, then answers using those passages — with a citation for each
claim, naming the document, the version, and where in it the passage sits.

If an answer has no citation, treat it with suspicion. The citation is the
part you can check, and checking it is the point.

Some sources are marked **via graph**. Primer found those by following the
links between people, places, and ideas in your library, not by how closely
the passage's wording matches the question. The source names what connected
it. A passage like that can look unrelated to what you asked, and the named
connections are how you judge whether it belongs.

!!! warning "Answers are drafts, not authorities"
    Retrieval finds passages that look relevant; a model then writes prose
    from them. Both steps can be wrong. The citations exist so you can
    verify the parts that matter, and you should.

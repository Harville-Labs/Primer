# Libraries

A library is the unit of privacy in Primer. It has one owner, and nobody
else can see it unless that owner shares it.

## What "private" means here

A library you have not been given access to is not merely unreadable — it is
undetectable. Asking Primer for one returns the same "not found" as asking
for a library that never existed. That is deliberate: an error that
distinguished "forbidden" from "missing" would let anyone confirm that a
particular library exists, which is itself a disclosure.

The same applies to documents inside a library, and to their contents in
search results.

## Creating and renaming

A library needs a name, which can be up to 120 characters, and can be
renamed at any time. Renaming does not affect its documents.

If two people edit the same library, the second change can be rejected
rather than silently overwriting the first. Reload and reapply the change.

## Deleting

Deleting a library takes it and its documents out of reach immediately. The
underlying passages and files are removed afterwards, in the background.

The order matters: the tombstone is written first, so there is no window in
which a deleted library still answers questions. What follows is cleanup,
not part of the deletion the user asked for.

## Sharing

The owner of a library can let other people read it. Open the library and
name them by email address under **Shared with**.

Sharing gives read access and nothing else. Someone a library is shared with
can open it, read and download its documents, and ask questions answered
from it — with the same citations the owner gets. They cannot add documents,
rename it, delete it, or share it onward. Those stay with the owner, because
a share says nothing about them.

Nothing is copied. Both people read the same library, the same files and the
same passages, so sharing costs nothing and a document added by the owner is
immediately available to everyone it is shared with.

### Who you can share with

Only people who have signed in to this deployment at least once. Primer
learns that someone exists when they first use it, so an address it has
never seen is refused rather than accepted and quietly left dormant — which
would leave you believing you had shared something that nobody could open.

If your identity provider reports the same address for more than one
account, Primer refuses to guess between them and says so.

### Stopping

Removing someone takes effect on their next request. There is nothing to
clean up and no window to wait out: access is checked when it is used, and
because sharing never copied anything there is no second copy left holding
the material.

They are not told. The library simply stops appearing for them.

### What it looks like from the other side

A library shared with you appears in your list marked *Shared*, and in the
library picker when you ask a question. What you cannot do with it is not
offered rather than offered and refused.

## Duplicating a library

Copying a library gives you a second one you can change without touching the
first. Add to one, delete from the other; they share nothing that either can
edit.

What they do share is the stored files themselves, which are identical bytes
and never change, so a copy costs almost nothing to keep.

The passages behind a copy are rebuilt rather than copied over, so its
documents arrive queued and become searchable as they finish indexing — the
same as documents you have just uploaded. Browsing it works immediately.

The copy belongs to you, even if you copied a library someone shared with
you, and it is named after the original unless you give it a name. That is
the way to keep something that has been shared with you: a copy is yours,
and stays yours if the original is unshared.

## Asking about the whole library

A question is normally answered from the passages that most resemble it.
That suits "what did the 2019 trial find?" and fails "what are the main
themes here?", because the passages nearest that question all come from one
corner of the library.

Switch on **Across the library**, beside the library picker, before asking a
question of that kind. Primer then reads a passage or two from each of the
library's main topics and answers from those, still citing each one. The
answer covers breadth rather than depth: it names a theme and cites an
example of it.

This needs the knowledge graph. Without one, the question gets an ordinary
answer. A library too large for the deployment to group into topics is
refused, with a message saying so.

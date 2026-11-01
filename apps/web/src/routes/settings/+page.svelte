<script lang="ts">
	import { enhance } from '$app/forms';
	import { Check, Plus, TriangleAlert, Trash2, X } from '@lucide/svelte';
	import { Alert, Badge, Button, Checkbox, Input } from '@sivir-ui/svelte';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	//: Whether this deployment can hold a key at all. A provider added
	//: without one still works - most local servers ignore it - so the form
	//: stays usable and only the key field is withdrawn.
	let canStoreKeys = $derived(
		(data.providers ?? []).some((provider) => provider.api_key_set) ||
			!(form?.error ?? '').includes('encryption key')
	);

	//: What a provider can be declared to return, for scoring. Named here so
	//: the list and the add form cannot describe them differently.
	const declarations = [
		{ flag: 'supports_logprobs', label: 'Logprobs' },
		{ flag: 'supports_prompt_logprobs', label: 'Prompt logprobs' },
	] as const;

	let declaring = $state({ supports_logprobs: false, supports_prompt_logprobs: false });
</script>

<div class="mx-auto w-full max-w-4xl">
	<h1 class="text-xl font-semibold tracking-[-0.02em]">Settings</h1>
	<p class="mt-1 max-w-2xl text-sm text-muted-foreground">
		How this deployment is wired, and where its answers come from. Visible to administrators
		only.
	</p>

	{#if form?.error}
		<Alert.Root variant="error" class="mt-6">
			<Alert.Description>{form.error}</Alert.Description>
		</Alert.Root>
	{/if}

	<!--
	  Connections first. This is the page someone opens when something is
	  already wrong, and the question they arrived with is almost always
	  "what is Primer pointed at, and is it answering".
	-->
	<section class="mt-8">
		<h2 class="font-mono text-[10px] uppercase tracking-[0.09em] text-muted-foreground">
			Connections
		</h2>

		{#if data.status === null}
			<!--
			  Said plainly. A blank panel here would be read as "nothing is
			  configured", which is a very different thing from "the service
			  that knows could not be asked".
			-->
			<Alert.Root variant="error" class="mt-3">
				<Alert.Title>Could not read this deployment's status</Alert.Title>
				<Alert.Description>
					Control did not answer, so what follows is unknown rather than empty.
				</Alert.Description>
			</Alert.Root>
		{:else}
			<dl class="mt-3 grid gap-2 sm:grid-cols-2">
				<div class="rounded-lg border border-border bg-card p-3">
					<dt class="text-xs text-muted-foreground">Authentication</dt>
					<dd class="mt-0.5 font-medium">
						{data.status.auth_mode === 'oidc' ? 'OIDC, through the proxy' : 'Disabled'}
					</dd>
					{#if data.status.auth_mode === 'oidc' && !data.status.admin_group}
						<!--
						  Worth saying on the one page it governs: with no group
						  named, nobody is an administrator, and only a
						  deployment with authentication off can see this at all.
						-->
						<dd class="mt-1 text-xs text-warning">
							No administrator group is named, so nobody qualifies.
						</dd>
					{:else if data.status.admin_group}
						<dd class="mt-1 font-mono text-xs text-muted-foreground">
							Administrators: {data.status.admin_group}
						</dd>
					{/if}
				</div>

				{#each data.status.dependencies as dependency (dependency.name)}
					<div class="rounded-lg border border-border bg-card p-3">
						<dt class="flex items-center gap-2 text-xs text-muted-foreground">
							{dependency.name}
							<Badge variant={dependency.reachable ? 'success' : 'destructive'}>
								{dependency.reachable ? 'Answering' : 'Not answering'}
							</Badge>
						</dt>
						{#if dependency.url}
							<!--
							  Credentials are stripped before this leaves the
							  server, so what is printed here is the address and
							  nothing else.
							-->
							<dd class="mt-1 truncate font-mono text-xs" title={dependency.url}>
								{dependency.url}
							</dd>
						{/if}
						<dd class="mt-0.5 text-xs text-muted-foreground">{dependency.detail}</dd>
					</div>
				{/each}
			</dl>
		{/if}
	</section>

	<!--
	  Providers second: the part an administrator comes here to change rather
	  than to read.
	-->
	<section class="mt-10">
		<h2 class="font-mono text-[10px] uppercase tracking-[0.09em] text-muted-foreground">
			Inference providers
		</h2>
		<p class="mt-1 max-w-2xl text-sm text-muted-foreground">
			Every endpoint this deployment can answer from. Models from all of them appear
			together in the chat window, each labelled with the provider serving it.
		</p>

		{#if data.providers === null}
			<Alert.Root variant="error" class="mt-3">
				<Alert.Description>
					Chat did not answer, so the providers it holds could not be listed.
				</Alert.Description>
			</Alert.Root>
		{:else}
			<ul class="mt-3 space-y-2">
				{#each data.providers as provider (provider.id)}
					<li
						class="flex flex-wrap items-center gap-3 rounded-lg border border-border
							bg-card p-3 {provider.enabled ? '' : 'opacity-60'}"
					>
						<span class="min-w-0 flex-1">
							<span class="flex items-center gap-2">
								<span class="font-medium">{provider.name}</span>
								{#if provider.source === 'deployment'}
									<!--
									  It comes from the chart and changes by
									  redeploying. Saying so is what stops an
									  operator hunting for a control that is
									  deliberately not here.
									-->
									<Badge variant="secondary">From the deployment</Badge>
								{/if}
								{#if !provider.enabled}
									<Badge variant="warning">Disabled</Badge>
								{/if}
							</span>
							<span class="mt-0.5 block truncate font-mono text-xs text-muted-foreground">
								{provider.base_url}
							</span>
						</span>

						<span
							class="flex items-center gap-1.5 text-xs
								{provider.api_key_set ? 'text-muted-foreground' : 'text-muted-foreground'}"
							title={provider.api_key_set
								? 'An API key is stored for this provider. It cannot be read back.'
								: 'No API key is stored. Most local servers need none.'}
						>
							{#if provider.api_key_set}
								<Check size={13} aria-hidden="true" /> Key stored
							{:else}
								<X size={13} aria-hidden="true" /> No key
							{/if}
						</span>

						<!--
						  Declared, never probed: a server without logprobs
						  usually answers anyway with the field left out, which
						  would score as a model that is never surprised.
						-->
						<span class="flex items-center gap-1">
							{#each declarations as { flag, label } (flag)}
								{#if provider.source === 'deployment'}
									<Badge variant={provider[flag] ? 'secondary' : 'outline'}>
										{label}{provider[flag] ? '' : ': not declared'}
									</Badge>
								{:else}
									<form method="POST" action="?/declare" use:enhance>
										<input type="hidden" name="id" value={provider.id} />
										<input type="hidden" name="flag" value={flag} />
										<input type="hidden" name="value" value={provider[flag] ? 'false' : 'true'} />
										<Button
											type="submit"
											variant="ghost"
											size="sm"
											class="h-7 gap-1 text-xs {provider[flag] ? '' : 'text-muted-foreground'}"
											title={provider[flag]
												? `${label} declared. Click to withdraw the declaration.`
												: `${label} not declared. Click to declare that this server returns them.`}
										>
											{#if provider[flag]}
												<Check size={12} aria-hidden="true" />
											{:else}
												<X size={12} aria-hidden="true" />
											{/if}
											{label}
										</Button>
									</form>
								{/if}
							{/each}
						</span>

						{#if provider.source !== 'deployment'}
							<span class="flex items-center gap-1">
								<form method="POST" action="?/toggle" use:enhance>
									<input type="hidden" name="id" value={provider.id} />
									<input
										type="hidden"
										name="enabled"
										value={provider.enabled ? 'false' : 'true'}
									/>
									<Button type="submit" variant="ghost" size="sm">
										{provider.enabled ? 'Disable' : 'Enable'}
									</Button>
								</form>
								<form
									method="POST"
									action="?/remove"
									use:enhance={({ cancel }) => {
										if (!confirm(`Remove ${provider.name}?`)) cancel();
									}}
								>
									<input type="hidden" name="id" value={provider.id} />
									<Button
										type="submit"
										variant="ghost"
										size="icon"
										class="text-muted-foreground"
										title="Remove"
									>
										<Trash2 size={14} aria-hidden="true" />
										<span class="sr-only">Remove {provider.name}</span>
									</Button>
								</form>
							</span>
						{/if}
					</li>
				{/each}
			</ul>

			{#if data.providers.length === 0}
				<p class="mt-3 text-sm text-muted-foreground">
					None yet. Add one below, or configure one in the chart.
				</p>
			{/if}

			<form
				method="POST"
				action="?/add"
				use:enhance
				class="mt-4 rounded-lg border border-dashed border-border p-3"
			>
				<div class="grid gap-2 sm:grid-cols-[1fr_2fr_1fr_auto]">
					<Input name="name" required maxlength={80} placeholder="Name" aria-label="Provider name" />
					<Input
						name="base_url"
						required
						placeholder="https://host/v1"
						aria-label="Base URL"
					/>
					<!--
					  Write-only. It is never sent back to the browser, so the
					  field is always empty even for a provider that has one -
					  which is why the list says whether a key is stored.
					-->
					<Input
						name="api_key"
						type="password"
						placeholder="API key (optional)"
						aria-label="API key"
						autocomplete="off"
					/>
					<Button type="submit">
						<Plus size={15} aria-hidden="true" />
						Add
					</Button>
				</div>
				<div class="mt-2 flex flex-wrap items-center gap-4 text-xs">
					{#each declarations as { flag, label } (flag)}
						<!-- Sivir's checkbox names no input of its own, so the form reads this. -->
						<Checkbox bind:checked={declaring[flag]} label="Returns {label.toLowerCase()}" />
						<input type="hidden" name={flag} value={String(declaring[flag])} />
					{/each}
					<span class="text-muted-foreground">
						For scoring on the Anomalies tab. Prompt logprobs need <span class="font-mono"
							>echo</span
						> on /completions, which vLLM supports.
					</span>
				</div>
				{#if !canStoreKeys}
					<p class="mt-2 flex items-center gap-1.5 text-xs text-warning">
						<TriangleAlert size={13} aria-hidden="true" />
						This deployment has no encryption key configured, so an API key cannot be
						stored. Endpoints that need none still work.
					</p>
				{/if}
			</form>
		{/if}
	</section>
</div>

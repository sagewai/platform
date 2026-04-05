# Sagewai License FAQ

## General Questions

### Can I use Sagewai for free?

Yes. Sagewai is free to use under the AGPL-3.0 license. You can use it for personal projects, commercial projects, and enterprise deployments at no cost, as long as you comply with the AGPL-3.0 terms.

### Can I use Sagewai in my company?

Yes. Companies of any size can use Sagewai internally without paying a license fee. The AGPL-3.0 only requires you to share modifications if you offer the modified software as a service to third parties.

### Are my agents and workflows proprietary?

Yes. Your agent code, workflows, prompts, tools, and business logic are yours. They are not derivative works of Sagewai. You own everything you build with Sagewai.

### What triggers AGPL-3.0 obligations?

If you **modify** the Sagewai SDK itself and then offer the modified version as a network service to third parties, you must make your modifications available under AGPL-3.0. Simply using Sagewai as infrastructure does not trigger this.

## Derivative Works

### What counts as a derivative work?

Under the AGPL-3.0 and copyright law, a derivative work includes:

- Modifying the Sagewai source code
- Copying substantial portions of Sagewai code into another project
- **Translating the Sagewai codebase to another programming language**

### Does translating Sagewai to another language create a derivative work?

**Yes.** Translating the Sagewai codebase (or substantial portions of it) from Python to TypeScript, Go, Rust, Java, C++, or any other programming language constitutes a derivative work under international copyright law and the AGPL-3.0 license.

Such translations:

1. **Must** retain the AGPL-3.0 license (or a compatible copyleft license)
2. **Must** provide clear attribution to Ali Arda Diri as the original author
3. **Must not** use the Sagewai name, logo, or "Sage" prefix (see TRADEMARK.md)
4. **Must** make their source code publicly available under AGPL-3.0

This applies regardless of whether the translation is:
- Manual (human-written)
- Automated (tool-assisted)
- AI-assisted (using code generation models like Copilot, Codex, Claude, etc.)

### What about unofficial thin client wrappers?

Thin API client wrappers that call the Sagewai REST/WebSocket API are **not** derivative works. They are interoperable software. However, official Sagewai client wrappers exist for 14 languages (see the main README), so we encourage using those.

### What if I only copy a small part of Sagewai?

Copying a small, isolated utility function (e.g., a helper to parse JSON) is likely fair use and not a derivative work. Copying the architecture, class hierarchy, API design, or substantial logic from multiple modules is a derivative work.

## SaaS and Hosting

### Can I offer Sagewai as a hosted service?

Yes, but you must comply with AGPL-3.0:
- Make the complete source code of your service available to users
- Include all modifications under AGPL-3.0
- Maintain Sagewai attribution and branding (see TRADEMARK.md)

Alternatively, obtain a commercial license to avoid AGPL-3.0 obligations.

### Can I rebrand Sagewai and sell it?

No. You may not rebrand Sagewai and offer it as your own product. See TRADEMARK.md for details. If you want to white-label Sagewai, you need an Enterprise commercial license.

## Official Client Wrappers

### Why does Sagewai offer official wrappers in so many languages?

Sagewai is building official thin API client wrappers in 14 languages (TypeScript, Go, Rust, Java, C#, Ruby, C++, Swift, PHP, Dart, Perl, Elixir, Scala) so that developers in any language can integrate with Sagewai without needing to translate the core SDK. These wrappers are being released progressively — check the [Sagewai GitHub organization](https://github.com/sagewai) for availability.

These wrappers call the Sagewai server API and are maintained by Ali Arda Diri. They are the recommended way to use Sagewai from non-Python environments.

## Contact

For licensing questions not covered here: licensing@sagewai.ai

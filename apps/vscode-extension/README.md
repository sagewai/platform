# Sagewai VS Code Extension

Directive syntax highlighting and agent scaffolding for the [Sagewai SDK](https://github.com/sagecurator/atelier).

## Features

### Directive Syntax Highlighting

Highlights Sagewai directive syntax in Python and Markdown files:

- `@context('query', scope='project', tags='finance')` -- context retrieval
- `@memory('query')` -- memory search
- `@agent:name('task')` -- agent delegation
- `@wf:name('input')` -- workflow invocation
- `/tool.name('args')` -- tool calls
- `#model:gpt-4o`, `#budget:1.00` -- meta directives
- `@datetime`, `@date`, `@time`, `@user`, `@project` -- dynamic parameters
- `{{ context.search('q') }}` -- template expressions

### Agent Scaffolding Commands

Open the Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`) and run:

| Command | Description |
|---------|-------------|
| **Sagewai: New Agent** | Generate a complete agent class with an example tool |
| **Sagewai: New Workflow** | Generate a sequential multi-agent workflow |
| **Sagewai: Add Tool** | Insert a `@tool`-decorated function at the cursor |

### Snippets

Type a prefix and press `Tab` to expand:

| Prefix | Description |
|--------|-------------|
| `swagent` | Full agent with tool and configuration |
| `swtool` | `@tool`-decorated function |
| `swworkflow` | Sequential agent workflow |
| `swctx` | `@context()` directive |
| `swmem` | `@memory()` directive |
| `swag` | `@agent:name()` directive |

## Installation

### From VSIX

1. Build the extension:

   ```bash
   cd clients/vscode
   npm install
   npm run compile
   npx @vscode/vsce package
   ```

2. Install the `.vsix` file in VS Code:
   - Open Command Palette > **Extensions: Install from VSIX...**
   - Select the generated `sagewai-0.1.0.vsix` file

### From Source (Development)

1. Open `clients/vscode/` in VS Code
2. Press `F5` to launch the Extension Development Host
3. The extension activates automatically for Python and Markdown files

## Requirements

- VS Code 1.85.0 or later
- No runtime dependencies (syntax and snippets are fully declarative)

## License

Part of the [Sagewai SDK](https://github.com/sagecurator/atelier) project by Sagecurator.

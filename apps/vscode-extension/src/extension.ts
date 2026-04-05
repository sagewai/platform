import * as vscode from 'vscode';

// ── Python code templates ──────────────────────────────────────────

function agentTemplate(className: string, snakeName: string): string {
    return `"""${className} — AI agent powered by Sagewai SDK."""

from __future__ import annotations

from sagewai import UniversalAgent, tool, ToolSpec


@tool
def example_tool(query: str) -> str:
    """Example tool — replace with your implementation."""
    return f"Result for: {query}"


class ${className}(UniversalAgent):
    """${className} agent.

    Usage::

        agent = ${className}()
        result = await agent.chat("Hello")
    """

    def __init__(self, **kwargs):
        super().__init__(
            name="${snakeName}",
            model="gpt-4o",
            system_prompt="You are a helpful AI assistant.",
            tools=[example_tool],
            **kwargs,
        )
`;
}

function workflowTemplate(name: string): string {
    return `"""${name} — multi-agent workflow."""

from __future__ import annotations

from sagewai import SequentialAgent, UniversalAgent


# Stage 1: Research
researcher = UniversalAgent(
    name="researcher",
    model="gpt-4o",
    system_prompt="You are a research assistant.",
)

# Stage 2: Writer
writer = UniversalAgent(
    name="writer",
    model="gpt-4o",
    system_prompt="You are a writer. Use the research to produce content.",
)

# Pipeline
pipeline = SequentialAgent(
    name="${name}",
    agents=[researcher, writer],
)


async def run(input_text: str) -> str:
    """Run the workflow."""
    return await pipeline.chat(input_text)
`;
}

// ── Extension entry point ──────────────────────────────────────────

export function activate(context: vscode.ExtensionContext) {
    // New Agent command
    context.subscriptions.push(
        vscode.commands.registerCommand('sagewai.newAgent', async () => {
            const name = await vscode.window.showInputBox({
                prompt: 'Agent name (PascalCase)',
                placeHolder: 'MyAgent',
            });
            if (!name) return;

            const snakeName = name
                .replace(/([A-Z])/g, '_$1')
                .toLowerCase()
                .replace(/^_/, '');
            const doc = await vscode.workspace.openTextDocument({
                content: agentTemplate(name, snakeName),
                language: 'python',
            });
            await vscode.window.showTextDocument(doc);
        })
    );

    // New Workflow command
    context.subscriptions.push(
        vscode.commands.registerCommand('sagewai.newWorkflow', async () => {
            const name = await vscode.window.showInputBox({
                prompt: 'Workflow name',
                placeHolder: 'my_workflow',
            });
            if (!name) return;

            const doc = await vscode.workspace.openTextDocument({
                content: workflowTemplate(name),
                language: 'python',
            });
            await vscode.window.showTextDocument(doc);
        })
    );

    // Add Tool command
    context.subscriptions.push(
        vscode.commands.registerCommand('sagewai.addTool', async () => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) return;

            const toolName = await vscode.window.showInputBox({
                prompt: 'Tool function name',
                placeHolder: 'my_tool',
            });
            if (!toolName) return;

            const snippet = new vscode.SnippetString(
                `@tool\ndef ${toolName}(\${1:query}: str) -> str:\n` +
                `    \"\"\"\${2:Tool description}.\"\"\"\n    \${0:return "result"}\n\n`
            );
            await editor.insertSnippet(snippet);
        })
    );
}

export function deactivate() {}

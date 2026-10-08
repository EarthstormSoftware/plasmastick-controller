// Handles vscode://earthstorm.micropico-bridge/<action>, where <action> is
// "disconnect" or "connect", by running the matching MicroPico command.
// scripts/deploy.ps1 uses this so mpremote can open the serial port.
const vscode = require('vscode');

const ACTIONS = ['disconnect', 'connect'];

function activate(context) {
    context.subscriptions.push(vscode.window.registerUriHandler({
        async handleUri(uri) {
            const action = uri.path.replace(/^\//, '');
            if (ACTIONS.includes(action)) {
                await vscode.commands.executeCommand(`micropico.${action}`);
            }
        }
    }));
}

function deactivate() {}

module.exports = { activate, deactivate };

---
description: This prompt outlines the requirements for adding a new `copilot` command to the `syncript` CLI tool. The `copilot` command will allow users to execute the copilot CLI tool on a remote server, with the output being streamed back to the local terminal in real-time. The implementation includes features for logging, error handling, and managing copilot sessions, as well as a cleanup mechanism for old log files.
---

add a new command to syncript called `copilot` that will run the copilot CLI tool on the remote server. On the remote server, the executed `copilot <args>` command should be executed async (using nohup) and should be logged to a file called `copilot-{copilot-session-id}.log` in the `~/.sycnript/logs/` directory. The output of the copilot command should be streamed back to the local terminal in real-time. `copilot` command on server accepts a `--share <log-file-path>` arg which you could use to path the file address to it.

Copilot `<args>` requirements:
1. Generate a unique session ID for each copilot command execution (e.g., using UUID) and use it in the log file name.
2. Execute the copilot command asynchronously on the remote server using `nohup` and redirect both stdout and stderr to the log file.
5. Provide a way to view the log file for a specific copilot session after the command has been executed, such as a `syncript copilot logs <session-id>` command that retrieves and displays the contents of the corresponding log file.
6. Get the list of logs available in the `~/.syncript/logs/` directory and display them to the user with their corresponding session IDs and timestamps when the `syncript copilot logs` command is executed without a session ID argument.
6. Handle any errors that may occur during the execution of the copilot command and provide appropriate feedback to the user.
7. If the connection to the remote server is lost while the copilot command is running, ensure that the command continues to run on the remote server and that the log file is still being updated with the output of the command. Then try to reconnect and when the connection is re-established, stream any new output from the log file back to the local terminal in real-time.
8. Implement a cleanup mechanism to remove old log files after a certain period (e.g., 30 days) to prevent the logs directory from growing indefinitely. This can be done by adding a scheduled task on the remote server that periodically checks the logs directory and deletes files older than the specified retention period.
9. run the copilot command on the server with auto-pilot enabled using the `--yolo` flag.
10. Pass the prompt to the copilot commmand on the server using the `-p "Read '.copilot.prompt.md' file for the actual prompt"` flag.
11. Ensure that the copilot command is executed in the correct working directory on the remote server, which should be the same directory where the syncript command is being executed locally. This can be achieved by determining the current working directory on the local machine and then changing to that directory on the remote server before executing the copilot command.
12. Provide a way to stop a running copilot command on the remote server using a `syncript copilot stop <session-id>` command that sends a termination signal to the corresponding process on the remote server and updates the log file accordingly.
13. If no `--model` specified on local command (`sycript copilot`), use this : `--model claude-sonnet-4.6` as the default model for the copilot command on the remote server.

A sample f a `copilot` command execution on the server which should be wrapper in a `nohup` asyc command:
```bash
copilot --yolo -p "Read '.copilot.prompt.md' file for the actual prompt" --model claude-sonnet-4.6  --share  ~/.syncript/logs/copilot-123e4567-e89b-12d3-a456-426614174000.log
```

all new sycript commands should be added to the `syncript` CLI tool and should follow the same structure and conventions as the existing commands. The implementation should be modular and maintainable, allowing for easy future enhancements or modifications to the copilot command functionality.

List of new sycript commands to be added:
1. `syncript copilot <args>`: Executes the copilot command on the remote server with the specified arguments and streams the output back to the local terminal in real-time.
   *. All server arguments passed to the `syncript copilot` command should be forwarded to the copilot command on the remote server, with the addition of the `--share` argument to specify the log file path and the `--yolo` flag to enable auto-pilot mode. If no `--model` argument is provided, it should default to `claude-sonnet-4.6`.
2. `syncript copilot logs`: Lists all available copilot log files in the `~/.syncript/logs/` directory with their corresponding session IDs and timestamps.
3. `syncript copilot logs <session-id>`: Retrieves and displays the contents of the log file corresponding to the specified session ID.
4. `syncript copilot stop <session-id>`: Stops the running copilot command on the remote server corresponding to the specified session ID and updates the log file accordingly.

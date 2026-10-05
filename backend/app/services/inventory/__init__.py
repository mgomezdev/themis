"""Provider-agnostic core inventory services (BIZ-202 §3.3). Everything here talks to the *active* `filament_inventory`
provider through the plugin host and the neutral DTOs — never to a specific plugin, and only after a capability check."""

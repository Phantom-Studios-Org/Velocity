# Velocity

[![Build Status](https://img.shields.io/github/actions/workflow/status/PaperMC/Velocity/gradle.yml)](https://papermc.io/downloads/velocity)
[![Join our Discord](https://img.shields.io/discord/289587909051416579.svg?logo=discord&label=)](https://discord.gg/papermc)

> [!NOTE]
> **Phantom fork.** Sends the backend's own address in the handshake instead of
> the virtual host the player connected with.
>
> Upstream forwards the player's vhost on purpose (PaperMC/Velocity#1076), which
> works when the proxy is the edge and everything behind it is addressed by
> ip:port. It doesn't when the backend sits behind another hostname-based router,
> as it does on our hosting: there that field is the routing key for the next hop,
> so the player's vhost either fails to resolve or loops the connection back to
> the proxy it just left.
>
> Also applied to legacy and BungeeGuard forwarding, where the host is part of the
> string the backend validates.
>
> **Handshake token.** With `phantom-token` set, the proxy appends
> `\0phantom:<token>` to the handshake host so the node's router can tell which
> proxy is calling and refuse anything else. Modern forwarding only — legacy and
> BungeeGuard already own that field. The panel generates the token, rewrites it
> on every start, and leaves it empty when any backend behind the proxy is not
> ours: it's a private protocol and has no business on someone else's box.
>
> Pings carry it as well as logins. A backend that refuses anything but its proxy
> refuses status requests too, so without it the entry in the player's list goes
> dead while the server behind it is perfectly fine.
>
> Patches live in [`phantom/`](phantom/). Everything else is upstream.

A Minecraft server proxy with unparalleled server support, scalability,
and flexibility.

Velocity is licensed under the GPLv3 license.

## Goals

* A codebase that is easy to dive into and consistently follows best practices
  for Java projects as much as reasonably possible.
* High performance: handle thousands of players on one proxy.
* A new, refreshing API built from the ground up to be flexible and powerful
  whilst avoiding design mistakes and suboptimal designs from other proxies.
* First-class support for Paper, Sponge, Fabric and Forge. (Other implementations
  may work, but we make every endeavor to support these server implementations
  specifically.)
  
## Building

Velocity is built with [Gradle](https://gradle.org). We recommend using the
wrapper script (`./gradlew`) as our CI builds using it.

It is sufficient to run `./gradlew build` to run the full build cycle.

## Running

Once you've built Velocity, you can copy and run the `-all` JAR from
`proxy/build/libs`. Velocity will generate a default configuration file
and you can configure it from there.

Alternatively, you can get the proxy JAR from the [downloads](https://papermc.io/downloads/velocity)
page.

# Localisation

Translations are handled using [Crowdin](https://papermc-io.crowdin.com/velocity).
If you want to translate a language not available on Crowdin,
you might want to ask in the [Discord](https://discord.gg/papermc) about it.

The `demo-*` files are firmware binaries used by the engine tests. They are not kept in git
(they are Shanling's and Q2 Pod's). To recreate them: `demo-stock` is `release/bin/demo` from the
stock V1.36 rootfs, `demo-pod-stubbed` the same file from a Q2 Pod build after the emulator has
loaded it once, `demo-stock-stubbed` the stock one after the same.

**Winebundle for Batocera** installs Windows games right on your Batocera machine and adds
them to Batocera's **Windows** menu, ready to play with a controller.

### Install or update

On the Batocera machine (over SSH, or in F1 → Applications → xterm), run:

```sh
curl -fsSL https://raw.githubusercontent.com/jacobm36/winebundle-orchestrator/master/packaging/batocera/get-winebundle.sh | sh
```

Then open **Winebundle** from F1 → Applications or from **Ports**. Running the command
again updates it; your games are kept. Tested on Batocera 43 (x86 PC).
See the [Batocera guide](https://github.com/jacobm36/winebundle-orchestrator/blob/master/docs/BATOCERA.md).

# Molding workbench for FreeCAD

Builds a printable mould around an existing part. Aimed at hobby and workshop
silicone casting: you model a flexible part, and this makes the thing you print
to cast it in.

Requires FreeCAD 1.0 or newer. Uses the `Part` module only, so there is nothing
to install alongside it.

## Installing

Clone into your FreeCAD `Mod` folder:

```
git clone https://github.com/example/FreeCAD-Molding
```

| Platform | Mod folder |
| :- | :- |
| Linux | `~/.local/share/FreeCAD/Mod/` |
| Windows | `%APPDATA%\FreeCAD\Mod\` |
| macOS | `~/Library/Application Support/FreeCAD/Mod/` |

Restart FreeCAD and pick **Molding** from the workbench list.

## The workflow

The toolbar runs left to right in the order you actually work.

1. **New mould.** Select your part and press it. A `MoldJob` appears in the
   tree with the pieces underneath, and the setup panel opens. The parting
   plane is seeded at the widest cross section of the part, which for an
   organic shape is usually where you wanted it anyway.
2. **Mould setup.** Confirm the part, set cure shrink if your silicone needs
   it, and aim the pull direction. Click a flat face or a straight edge in the
   3D view and press *From face or edge*, or take it from the camera.
3. **Parting and pieces.** Move the parting plane, choose two or three pieces,
   and size the registration keys. *Put it on the selection* moves the plane to
   whatever you have picked, so you can drive it off an edge loop around the
   part.
4. **Mould block.** Wall, floor and roof thickness, box or cylinder, pry slots
   and clamping bolts.
5. **Pouring and venting.** The overflow gutter, the pour port, vents, and the
   overpour allowance used to work out how much to mix.
6. **Rebuild, exploded view, report.** The report gives you cavity volume in
   millilitres, the material volume of each piece, and anything the build
   flagged.
7. **Export pieces.** One STL or STEP per piece, each laid parting face down.

Everything lives on the job object, so you can also drive it from the property
editor, or from a macro:

```python
from freecad.Molding.app import mold_job
job = mold_job.create(App.ActiveDocument, App.ActiveDocument.MyPart)
job.PartingOffset = 12.5
job.Layout = "Three piece, split the top"
mold_job.rebuild(job)
```

## Why the features are what they are

**The overflow gutter** is the one that matters for the pour and press method.
If you pour more silicone than the cavity holds and then push the other half
in, the surplus has to go somewhere. Without a moat around the cavity opening
it spreads across the whole parting face, holds the halves apart, and every
part comes out oversize across the split with a thick flash line. The gutter
takes the surplus and the relief channels through the land give it a route to
get there.

**Registration keys are tapered cones**, not hemispheres. A cone prints without
support on a flat parting face, self centres as the halves close, and the
clearance on the pocket absorbs the elephant foot that a printed male feature
always has.

**Three piece layout** cuts one half again, parallel to the pull axis. Bolt
those two together while the silicone cures, then take them off sideways.
That is how you release a part that a single split would trap.

**Export orientation** puts the parting face on the bed and the cavity opening
upward. The cavity then prints as a pocket, with no support inside it. That
surface is the one the silicone copies, so it is the one worth protecting.

**Undercut warnings are advisory.** A silicone mould flexes, so it releases
undercuts a rigid mould could not. The report tells you where they are and
leaves the call to you.

## Tests

The geometry needs a real kernel, so the tests run inside FreeCAD:

```
freecadcmd tests/test_build.py
```

## Not in scope yet

Non planar and stepped parting surfaces, side actions, injection moulding
features such as runners, gates and cooling channels, and mould flow of any
kind. The block, split and feature code is separated so those can be added
without disturbing what is here.

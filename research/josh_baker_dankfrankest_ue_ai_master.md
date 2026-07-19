# Josh Baker (@Dankfrankest) - Stealth Project NPC AI: Master Document

Compiled 2026-07-19 from full transcripts of the channel's devlog videos
(2026-07-04 through 2026-07-17). One video (Alpha Level, 2026-07-03) has
captions disabled; its title and context are used where relevant. Channel
RSS exposes the 15 most recent videos; if older videos exist they are not
covered here.

## Project Overview

- Solo developer Josh Baker, publishing daily short-form devlogs.
- Stealth game in Unreal Engine 5, currently in alpha ("Alpha Level" devlog
  marks a playable test level as of 2026-07-03).
- Direct inspiration: Splinter Cell (the developer's own comparison for the
  shadow-concealment system).
- A public demo is targeted for August 1, 2026 (stated in the 2026-07-16 video).
- A project Discord was opened around 2026-07-08.

## The NPC AI System

### 1. Perception

**Sight**
- Sight is not binary. An accumulation meter fills over time before an NPC
  goes on alert (a sync bug between the meter and actual alert timing was
  fixed 2026-07-08).
- Shadow concealment: the player can hide in darkness, Splinter Cell style.
  Concealment validity is computed against the level's global illumination
  (bounced light), not just direct spotlight line of sight - fixed 2026-07-15
  specifically to kill "easy cheese spots" where shaded-but-visible players
  were unfairly hidden.

**Hearing**
- Footsteps feed the perception system in all NPC states (a bug where
  footsteps were ignored during the agitated state was fixed 2026-07-08).

**Environmental awareness**
- Flashlight beams trigger the investigation state (fixed/confirmed 2026-07-08).
- Breaking lights: NPCs investigate the AREA the light affected, not the
  light source location (reworked 2026-07-17 because geometry could occlude
  the source point and suppress the reaction).
- Thrown objects: if an NPC sees an object in flight, he investigates the
  THROW ORIGIN rather than the landing spot (added 2026-07-05). An object
  thrown directly at an NPC's face is taken "quite personally" (direct
  aggression response).
- Bodies: corpses are detected even when moved significantly from the spot
  of death (fixed 2026-07-12).

### 2. Search and Investigation

- Levels are partitioned into zones, which the search AI reasons over.
- Lost-target search uses a self-modeling heuristic: each NPC asks "where
  would I go if I wanted to hide from myself" and focuses on the farthest
  obstruction toward that candidate hiding spot until it is cleared or a new
  movement goal arrives. The developer calls the visible result a
  corner-spying rotation effect and considers it much better than facing
  movement direction (2026-07-06; refined 2026-07-10 so reacquired targets
  update the corner-prying focus).
- Squad search coordination (2026-07-07): NPCs first converge on the last
  known location, then divide and conquer remaining zones by distance from
  that point - built to stop them clumping in one area.
- Convergence dwell tuning (2026-07-12): the initial convergence phase now
  requires more investigation time at the disruption point before fan-out;
  the earlier quick give-up handed the player too much slack.

### 3. Combat

- Projectiles are fully simulated with per-weapon speeds; pistols and rifles
  differ, and slow rounds require leading targets at range (2026-07-04).
- Ammo awareness (2026-07-13): out-of-ammo NPCs either reload (if a magazine
  is available) or flee to a designated favorite safe spot. Safe spots are
  authored locations; the developer noted needing more of them. A melee
  fallback for some NPC types is planned but not yet implemented.
- Aim: pitch/yaw smoothing reworked 2026-07-17 (an inverted-recoil regression
  from that rework was caught and fixed same day).

### 4. Roles and Squads

- NPCs carry squad IDs.
- VIP assassination target (2026-07-09): the VIP seeks shelter when any of
  his teammates is alerted, and squad-ID mates become his royal guard. This
  makes open combat a deliberate tradeoff for the player.

### 5. NPC States (as referenced across devlogs)

- Calm/patrol (implied baseline)
- Investigation (triggered by flashlight, broken lights, thrown objects,
  sounds)
- Agitated (heightened state; previously had the footstep-deafness bug)
- Alert/hunting (post-accumulation; drives convergence then zone spread)
- Flee/shelter (ammo-out cowards; VIP shelter-seeking)

### 6. Player Systems That Feed the AI Loop

- Grip-loss meter (2026-07-11): hanging from ledges is time-limited to stop
  the player cheesing NPC searches by dangling indefinitely.
- Physics feel (2026-07-16): object physics tuned heavier than UE defaults,
  explicitly to escape the "standard Unreal feel."
- Run grading (2026-07-14): end screen reports run length, havoc, and an
  overall grade under a chosen playstyle - ghost, predator, or berserker.
  S-rank is achievable in any style by maximizing that style's criteria;
  stealth purity is not the only judged axis.

## Development Timeline

| Date | Devlog | AI-relevant change |
|---|---|---|
| 2026-07-03 | Alpha Level | Test level milestone (no captions available) |
| 2026-07-04 | Ballistic Physics | Per-weapon projectile speeds, target leading |
| 2026-07-05 | Smarter NPCs | Thrown-object origin investigation, face-hit aggression |
| 2026-07-06 | Pie Corners | Hide-from-myself search heuristic, corner-spying |
| 2026-07-07 | NPC Squad Strategy | Zone-based converge then divide-and-conquer |
| 2026-07-08 | Fixing NPC Logic | Flashlight trigger, agitated-state hearing, meter sync |
| 2026-07-09 | VIP NPC takes shelter | VIP shelter + royal guard via squad ID |
| 2026-07-10 | More Look At Fixes | Orientation fixes, corner-pry target updates |
| 2026-07-11 | Grip Loss | Anti-cheese ledge timer |
| 2026-07-12 | Endless NPC Tweaks | Moved-body detection, convergence dwell time |
| 2026-07-13 | COWARDS | Ammo-out reload/flee, safe spots, melee planned |
| 2026-07-14 | Replayability | Ghost/predator/berserker grading |
| 2026-07-15 | Splinter-Cell Shadow Fix | GI-aware shadow concealment |
| 2026-07-16 | Heavier physics | Heavier-than-default physics, Aug 1 demo target |
| 2026-07-17 | NPCs React to Breakable Lights | Area-based light-break reaction, aim rework |

## Design Philosophy (observed patterns)

1. Anti-cheese is a constant driver: GI-aware shadows, grip loss, moved-body
   detection, and dwell-time tuning all exist to close player exploits.
2. Believability over correctness: the search heuristic is framed around how
   the watching player reads NPC behavior (corner-spying "looks a lot nicer"),
   not around optimal search.
3. Difficulty is tuned via information and time, not stats: give-up timers,
   accumulation meters, and search spread control player slack.
4. The developer engages the audience for design input (recurring "what
   should I work on next" and "how could I make this better" prompts).
5. Cadence is daily, single-topic, iterative - most systems get a follow-up
   fix devlog within days of introduction.

## Open Questions / Not Yet Covered in Devlogs

- Underlying implementation: no mention of whether behavior trees, state
  trees, custom utility AI, or UE5's built-in perception components are used.
- Game title: the project is still unnamed publicly ("stealth project").
- Level/mission structure beyond the single alpha test level.
- Whether pre-July-3 videos exist (RSS window limitation).

## Sources

All videos by Josh Baker (@Dankfrankest), youtube.com/@Dankfrankest,
channel ID UCWOf9GaQxUQWSmSln8ETvmA. Individual video links appear in the
timeline table dates above in the form youtube.com/watch?v=ID:
diMOqaJ6FkE, JfBNaONhyjQ, J2rynI0oK8Q, I48w0THNJqE, Cm03UtoGZXU, KtOa28fadI8,
Hias2K59AfU, rRPs3OaNH_k, 4ps8jhNvBSA, 9UWH0f0DaSc, 0iyR4JPzySU, LJLsB1IK_58,
4xFpXt4cJKE, l2ue6B-sU9I, knXYdA4lDco.

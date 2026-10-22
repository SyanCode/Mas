import { msg } from "@/lib/i18n";

/** Texts of the advice of mas.resources (id → text with its params). */
export const ADVICE: Record<string, string> = {
  "memory-limit": msg("Uses {used} MB per instance with no limit: cap it at {mb} MB so it cannot take the memory of the other apps."),
  "memory-high": msg("Uses {used} MB per instance, close to its {limit} MB limit: it slows down then restarts. Give it more or look for a leak."),
  sleep: msg("Almost no visits ({rpm} req/min): let it sleep after {min} minutes without a visit, it starts again on the next one."),
  autoscale: msg("{n} instances for {rpm} req/min: let the instances follow the traffic instead."),
  "minecraft-heap": msg("{name} reserves its {memory} at start, players or not: replace MEMORY with INIT_MEMORY=512M and MAX_MEMORY={memory}, plus JVM_XX_OPTS=-XX:G1PeriodicGCInterval=60000 so the memory goes back to the server when it is empty (Minecraft servers of Mas do it from their next redeploy)."),
  "next-standalone": msg("Add output: \"standalone\" to next.config: the server loads only the files it needs and uses much less memory."),
};

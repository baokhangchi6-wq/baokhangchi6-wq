package com.masterstrap.rbx.data

/**
 * Defines the single FastFlag Editor exception to structured-control priority.
 *
 * A visible toggle/dropdown/box normally owns every flag it exposes so the
 * exported value cannot disagree with the UI. Desync intentionally exposes its
 * S2 physics sender rate as an advanced numeric tuning value; no other
 * structured flag may be overridden by the editor.
 */
object FlagPrecedencePolicy {
    const val EDITABLE_S2_PHYSICS_SENDER_RATE = "DFIntS2PhysicsSenderRate"

    fun editableS2PhysicsSenderRate(
        editorFlags: List<FlagItem>,
        desyncEnabled: Boolean
    ): Int? {
        if (!desyncEnabled) return null
        return editorFlags.asReversed().firstNotNullOfOrNull { flag ->
            if (!flag.isEnabled ||
                flag.name.trim() != EDITABLE_S2_PHYSICS_SENDER_RATE
            ) {
                null
            } else {
                flag.value.trim().toIntOrNull()
            }
        }
    }
}

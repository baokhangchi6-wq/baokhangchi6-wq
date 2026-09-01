package com.masterstrap.rbx.ads

/** No SDK/Android dependency: decide before scheduling any advertising work. */
object AdEligibility {
    enum class Access { FREE, AD_FREE, UNRESOLVED }

    fun resolve(plan: String, hasSavedKey: Boolean): Access = when {
        plan.trim().lowercase() in setOf("plus", "max", "active") -> Access.AD_FREE
        // Preserve the existing saved-license bypass during legacy plan migration.
        hasSavedKey -> Access.AD_FREE
        plan.trim().equals("Free", ignoreCase = true) -> Access.FREE
        else -> Access.UNRESOLVED
    }
}

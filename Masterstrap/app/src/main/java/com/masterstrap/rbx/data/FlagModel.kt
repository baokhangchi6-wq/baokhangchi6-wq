package com.masterstrap.rbx.data

enum class FlagType {
    BOOLEAN,
    INTEGER,
    STRING
}

data class FlagItem(
    var name: String,
    var type: FlagType,
    var value: String,
    var isEnabled: Boolean = true,
    var category: String = "Custom"
)

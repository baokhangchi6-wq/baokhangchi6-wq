package com.masterstrap.rbx.ui

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import com.masterstrap.rbx.data.PresetToggle
import com.masterstrap.rbx.databinding.ItemPresetToggleBinding

class PresetToggleAdapter(
    private val toggles: List<PresetToggle>,
    private val onToggleChanged: (PresetToggle, Boolean) -> Unit
) : RecyclerView.Adapter<PresetToggleAdapter.ToggleViewHolder>() {

    inner class ToggleViewHolder(val binding: ItemPresetToggleBinding) : RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ToggleViewHolder {
        val binding = ItemPresetToggleBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return ToggleViewHolder(binding)
    }

    override fun onBindViewHolder(holder: ToggleViewHolder, position: Int) {
        val item = toggles[position]
        with(holder.binding) {
            tvToggleTitle.text = item.title
            tvToggleDesc.text = item.description
            ivToggleCrown.visibility = if (item.id == "game_mode_master") android.view.View.VISIBLE else android.view.View.GONE
            switchToggle.setOnCheckedChangeListener(null)
            switchToggle.isChecked = item.isEnabled

            switchToggle.setOnCheckedChangeListener { _, isChecked ->
                item.isEnabled = isChecked
                onToggleChanged(item, isChecked)
            }
        }
    }

    override fun getItemCount(): Int = toggles.size
}

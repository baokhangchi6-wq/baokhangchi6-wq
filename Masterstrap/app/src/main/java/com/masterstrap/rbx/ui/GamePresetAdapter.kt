package com.masterstrap.rbx.ui

import android.graphics.BitmapFactory
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import com.masterstrap.rbx.R
import com.masterstrap.rbx.data.GamePresetItem
import com.masterstrap.rbx.databinding.ItemGamePresetCardBinding
import com.masterstrap.rbx.databinding.ItemSectionHeaderBinding

sealed class GamePresetUIItem {
    data class Header(val title: String, val hasCrown: Boolean = false) : GamePresetUIItem()
    data class Preset(val preset: GamePresetItem) : GamePresetUIItem()
}

class GamePresetAdapter(
    private val items: List<GamePresetUIItem>,
    private var selectedPresetId: String = "none",
    private val onPresetBoosted: (GamePresetItem) -> Unit
) : RecyclerView.Adapter<RecyclerView.ViewHolder>() {

    companion object {
        private const val TYPE_HEADER = 0
        private const val TYPE_CARD = 1
    }

    fun setSelectedPreset(presetId: String) {
        selectedPresetId = presetId
        notifyDataSetChanged()
    }

    fun getSelectedPreset(): String = selectedPresetId

    override fun getItemViewType(position: Int): Int {
        return when (items[position]) {
            is GamePresetUIItem.Header -> TYPE_HEADER
            is GamePresetUIItem.Preset -> TYPE_CARD
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return when (viewType) {
            TYPE_HEADER -> {
                val binding = ItemSectionHeaderBinding.inflate(inflater, parent, false)
                HeaderViewHolder(binding)
            }
            TYPE_CARD -> {
                val binding = ItemGamePresetCardBinding.inflate(inflater, parent, false)
                CardViewHolder(binding)
            }
            else -> throw IllegalArgumentException("Unknown view type")
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        when (val item = items[position]) {
            is GamePresetUIItem.Header -> (holder as HeaderViewHolder).bind(item)
            is GamePresetUIItem.Preset -> (holder as CardViewHolder).bind(item.preset)
        }
    }

    override fun getItemCount(): Int = items.size

    inner class HeaderViewHolder(private val binding: ItemSectionHeaderBinding) :
        RecyclerView.ViewHolder(binding.root) {
        fun bind(item: GamePresetUIItem.Header) {
            binding.tvSectionTitle.text = item.title
            binding.ivSectionCrown.visibility = if (item.hasCrown) View.VISIBLE else View.GONE
        }
    }

    inner class CardViewHolder(private val binding: ItemGamePresetCardBinding) :
        RecyclerView.ViewHolder(binding.root) {
        fun bind(preset: GamePresetItem) {
            val context = binding.root.context
            val isSelected = preset.id == selectedPresetId

            binding.tvPresetTitle.text = preset.title
            binding.ivPresetCrown.visibility = if (preset.isPopular) View.VISIBLE else View.GONE

            // Thumbnail display from assets
            if (!preset.thumbPath.isNullOrEmpty()) {
                binding.cardThumbContainer.visibility = View.VISIBLE
                if (preset.thumbPath == "app_logo") {
                    binding.ivPresetThumb.setImageResource(R.drawable.masterstrap)
                } else {
                    try {
                        context.assets.open(preset.thumbPath).use { stream ->
                            val bmp = BitmapFactory.decodeStream(stream)
                            if (bmp != null) {
                                binding.ivPresetThumb.setImageBitmap(bmp)
                            } else {
                                binding.ivPresetThumb.setImageResource(R.drawable.masterstrap)
                            }
                        }
                    } catch (e: Exception) {
                        binding.ivPresetThumb.setImageResource(R.drawable.masterstrap)
                    }
                }
            } else {
                binding.cardThumbContainer.visibility = View.GONE
            }

            if (isSelected) {
                binding.cardGamePreset.setStrokeColor(ContextCompat.getColor(context, R.color.brand_primary))
                binding.cardGamePreset.strokeWidth = (2 * context.resources.displayMetrics.density).toInt()
                // A preset can be re-applied after the user edits individual
                // flags, so the action must never become a terminal "Boosted"
                // state. The selected card border remains the status indicator.
                binding.tvPresetActionBtn.text = context.getString(R.string.btn_boost)
                binding.tvPresetActionBtn.setBackgroundResource(R.drawable.bg_btn_compact)
                binding.tvPresetActionBtn.setTextColor(ContextCompat.getColor(context, R.color.card_white))
            } else {
                binding.cardGamePreset.setStrokeColor(ContextCompat.getColor(context, R.color.card_stroke))
                binding.cardGamePreset.strokeWidth = (1 * context.resources.displayMetrics.density).toInt()
                binding.tvPresetActionBtn.text = context.getString(R.string.btn_boost)
                binding.tvPresetActionBtn.setBackgroundResource(R.drawable.bg_btn_compact)
                binding.tvPresetActionBtn.setTextColor(ContextCompat.getColor(context, R.color.card_white))
            }

            binding.tvPresetActionBtn.setOnClickListener {
                onPresetBoosted(preset)
            }

            binding.cardGamePreset.setOnClickListener {
                onPresetBoosted(preset)
            }
        }
    }
}

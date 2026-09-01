package com.masterstrap.rbx.ui

import android.text.Editable
import android.text.TextWatcher
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.AdapterView
import android.widget.ArrayAdapter
import androidx.recyclerview.widget.RecyclerView
import com.masterstrap.rbx.R
import com.masterstrap.rbx.data.PresetDropdown
import com.masterstrap.rbx.data.PresetItemUI
import com.masterstrap.rbx.data.PresetToggle
import com.masterstrap.rbx.databinding.ItemPresetDropdownBinding
import com.masterstrap.rbx.databinding.ItemPresetFpsSliderBinding
import com.masterstrap.rbx.databinding.ItemPresetToggleBinding
import com.masterstrap.rbx.databinding.ItemSectionHeaderBinding

class PresetUIAdapter(
    private val items: List<PresetItemUI>,
    private var currentFps: Int = 60,
    private val onToggleChanged: (PresetToggle, Boolean) -> Unit = { _, _ -> },
    private val onDropdownChanged: (PresetDropdown, String) -> Unit = { _, _ -> },
    private val onFpsChanged: (Int) -> Unit = {}
) : RecyclerView.Adapter<RecyclerView.ViewHolder>() {

    companion object {
        private const val TYPE_HEADER = 0
        private const val TYPE_TOGGLE = 1
        private const val TYPE_DROPDOWN = 2
        private const val TYPE_FPS_SLIDER = 3

        val FPS_STEPS = intArrayOf(60, 120, 144, 240, 300)
    }

    fun setFps(fps: Int) {
        currentFps = fps
        notifyDataSetChanged()
    }

    override fun getItemViewType(position: Int): Int {
        return when (items[position]) {
            is PresetItemUI.Header -> TYPE_HEADER
            is PresetItemUI.Toggle -> TYPE_TOGGLE
            is PresetItemUI.Dropdown -> TYPE_DROPDOWN
            is PresetItemUI.FpsSlider -> TYPE_FPS_SLIDER
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return when (viewType) {
            TYPE_HEADER -> {
                val binding = ItemSectionHeaderBinding.inflate(inflater, parent, false)
                HeaderViewHolder(binding)
            }
            TYPE_TOGGLE -> {
                val binding = ItemPresetToggleBinding.inflate(inflater, parent, false)
                ToggleViewHolder(binding)
            }
            TYPE_DROPDOWN -> {
                val binding = ItemPresetDropdownBinding.inflate(inflater, parent, false)
                DropdownViewHolder(binding)
            }
            TYPE_FPS_SLIDER -> {
                val binding = ItemPresetFpsSliderBinding.inflate(inflater, parent, false)
                FpsSliderViewHolder(binding)
            }
            else -> throw IllegalArgumentException("Unknown view type $viewType")
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        when (val item = items[position]) {
            is PresetItemUI.Header -> (holder as HeaderViewHolder).bind(item)
            is PresetItemUI.Toggle -> (holder as ToggleViewHolder).bind(item.toggle)
            is PresetItemUI.Dropdown -> (holder as DropdownViewHolder).bind(item.dropdown)
            is PresetItemUI.FpsSlider -> (holder as FpsSliderViewHolder).bind(item)
        }
    }

    override fun getItemCount(): Int = items.size

    inner class HeaderViewHolder(private val binding: ItemSectionHeaderBinding) :
        RecyclerView.ViewHolder(binding.root) {
        fun bind(item: PresetItemUI.Header) {
            binding.tvSectionTitle.text = item.title
            binding.ivSectionCrown.visibility = if (item.hasCrown) View.VISIBLE else View.GONE
        }
    }

    inner class ToggleViewHolder(private val binding: ItemPresetToggleBinding) :
        RecyclerView.ViewHolder(binding.root) {
        fun bind(item: PresetToggle) {
            binding.tvToggleTitle.text = item.title
            binding.tvToggleDesc.text = item.description
            binding.ivToggleCrown.visibility = if (item.hasCrown || item.id == "game_mode_master") View.VISIBLE else View.GONE
            
            if (item.isLocked) {
                binding.root.alpha = 0.45f
                binding.switchToggle.setOnCheckedChangeListener(null)
                binding.switchToggle.isChecked = false
                binding.switchToggle.isEnabled = false
                binding.root.setOnClickListener {
                    onToggleChanged(item, false)
                }
            } else {
                binding.root.alpha = 1.0f
                binding.switchToggle.isEnabled = true
                binding.switchToggle.setOnCheckedChangeListener(null)
                binding.switchToggle.isChecked = item.isEnabled
                binding.root.setOnClickListener(null)

                binding.switchToggle.setOnCheckedChangeListener { _, isChecked ->
                    // ConfigExporter reads the in-memory PresetToggle objects when
                    // Save & Launch is pressed. Persisting the preference alone
                    // left this object stale until MainActivity was recreated.
                    item.isEnabled = isChecked
                    onToggleChanged(item, isChecked)
                }
            }
        }
    }

    inner class DropdownViewHolder(private val binding: ItemPresetDropdownBinding) :
        RecyclerView.ViewHolder(binding.root) {
        fun bind(item: PresetDropdown) {
            binding.tvDropdownTitle.text = item.title
            binding.tvDropdownDesc.text = item.description

            val context = binding.root.context
            val adapter = ArrayAdapter(
                context,
                R.layout.item_custom_spinner_item,
                item.options
            ).apply {
                setDropDownViewResource(R.layout.item_custom_spinner_dropdown)
            }
            binding.spinnerDropdown.adapter = adapter

            val selectedIndex = item.options.indexOf(item.selectedOption).coerceAtLeast(0)
            binding.spinnerDropdown.setSelection(selectedIndex, false)

            var isUserTouch = false
            binding.spinnerDropdown.setOnTouchListener { _, _ ->
                isUserTouch = true
                false
            }

            binding.spinnerDropdown.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                override fun onItemSelected(
                    parent: AdapterView<*>?,
                    view: View?,
                    pos: Int,
                    id: Long
                ) {
                    if (!isUserTouch) return
                    isUserTouch = false

                    val chosen = item.options[pos]
                    if (chosen != item.selectedOption) {
                        item.selectedOption = chosen
                        onDropdownChanged(item, chosen)
                    }
                }

                override fun onNothingSelected(parent: AdapterView<*>?) {}
            }
        }
    }

    inner class FpsSliderViewHolder(private val binding: ItemPresetFpsSliderBinding) :
        RecyclerView.ViewHolder(binding.root) {
        private var watcher: TextWatcher? = null

        fun bind(item: PresetItemUI.FpsSlider) {
            binding.tvFpsTitle.text = item.title
            binding.tvFpsDesc.text = item.description
            binding.etFpsInput.visibility = View.VISIBLE
            watcher?.let(binding.etFpsInput::removeTextChangedListener)
            val normalized = currentFps.coerceIn(30, 9999)
            if (binding.etFpsInput.text?.toString() != normalized.toString()) {
                binding.etFpsInput.setText(normalized.toString())
                binding.etFpsInput.setSelection(binding.etFpsInput.text?.length ?: 0)
            }
            watcher = object : TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
                override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
                override fun afterTextChanged(s: Editable?) {
                    val value = s?.toString()?.toIntOrNull() ?: return
                    if (value !in 30..9999 || value == currentFps) return
                    currentFps = value
                    onFpsChanged(value)
                }
            }
            binding.etFpsInput.addTextChangedListener(watcher)
            binding.etFpsInput.setOnFocusChangeListener { _, hasFocus ->
                if (hasFocus) return@setOnFocusChangeListener
                val value = binding.etFpsInput.text?.toString()?.toIntOrNull()
                    ?.coerceIn(30, 9999) ?: currentFps.coerceIn(30, 9999)
                currentFps = value
                onFpsChanged(value)
                if (binding.etFpsInput.text?.toString() != value.toString()) {
                    binding.etFpsInput.setText(value.toString())
                }
            }
        }
    }
}
